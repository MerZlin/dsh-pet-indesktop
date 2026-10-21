# -*- coding: utf-8 -*-
"""中途挂载（回填跳过 ``turn/start`` 的回合）→ ``turn/end`` 完成提醒回归。

issue #234 的残留子集：桌宠在**回合进行中**才接上 DSH 桥文件（应用启动、重新
开启联动、DSH 换实例产生新的 ``dsh-{pid}.jsonl``），``DirGlobTailer`` 的启动
回填防护把已经落盘的历史记录（``turn/start`` / ``tool/call`` …）整批跳到末尾
——呈现管线因此从没见过该 Agent 的任何状态（``_last_raw`` 无 dsh 键）。回合随后
以 ``turn/end`` 收尾时，``_on_agent_state`` 的完成边沿只认
``prev_raw in _BUSY_STATES``：``None`` 既不满足 busy、也不走 idle/error 分支，
回合**完全静默**（无完成气泡、无 done 音效）。

本模块的用例全部走真链路：真 ``DirGlobTailer``（**回填默认开着**——任何用例都
不得把 ``_initial_backfill_done`` 置真）+ 真 ``DshMonitor._poll``（与 worker
线程同一个入口）+ 真 ``DshStateConverger`` + 真 ``QTimer`` 完成确认窗 + 真
``AgentLinkManager``。只有窗口与音效播放漏斗是桩。

已登记的边界（本轮不改）：① ``turn/end`` 之前先到一条
``AgentStatus{idle}``（legacy 通路把 ``_last_raw`` 写成 "idle"）；② 关掉再开启
联动时 ``_last_raw`` 不随监视器生命周期清空，上一轮的终态（如 "success"）会
留给新接上的回合。这两种来向都不是 ``None``，``prev_raw is None`` 的放宽够不着
——属另案。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import pet.agent_link as agent_link
from pet import click_sound, harness_launcher
from pet.agent_link import dsh_state
from pet.agent_link import AgentLinkManager
from pet.config import Config
from pet.agent_link.dsh_state import DshState

_SUCCEED_WORDS = ("已完成本轮任务", "执行完成", "已顺利完成")


@dataclass
class _Rig:
    """真 manager + 真 DshMonitor（worker 不启动，手动驱动 ``_poll``）+ 桩窗口。"""

    mgr: AgentLinkManager
    win: object
    bubbles: list = field(default_factory=list)
    sounds: list = field(default_factory=list)
    switched: list = field(default_factory=list)
    redirected: list = field(default_factory=list)
    clock: list = field(default_factory=lambda: [1000.0])
    app: QApplication | None = None

    @property
    def mon(self):
        return self.mgr.monitors["dsh"]

    @property
    def bridge(self) -> Path:
        return self.mon.events_dir


def _make_rig(tmp_path, monkeypatch) -> _Rig:
    """构造真 manager/monitor；**不碰** ``_initial_backfill_done``（回填保持开着）。"""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    monkeypatch.setattr(harness_launcher, "desktop_process_running", lambda: True)

    bubbles: list[str] = []
    sounds: list[str] = []
    switched: list[str] = []
    redirected: list[tuple] = []

    class DummyWin:
        cats = {"acts": ["写代码", "吃Token"]}
        idles = ["待机呼吸"]
        _bubble_busy_until = 0.0
        _alert_current = None
        _alert_queue: list = []
        _sticky_bubble_active = False
        hidden_bubble_redirect = None

        def isVisible(self):
            return True

        def _switch(self, name):
            switched.append(name)

        def request_link_anim(self, name):
            switched.append("anim:" + str(name))

        def request_link_idle(self):
            switched.append("idle:" + self.idles[0])

        def show_bubble(self, text, duration_ms=3000, **_kw):
            bubbles.append(str(text))

        def show_alert(self, text, **_kw):
            bubbles.append(str(text))
            return "alert-1"

        def resolve_alert(self, alert_id):
            pass

        def hide_bubble(self):
            pass

        def _pick(self, lst):
            return lst[0]

    # 音效钉在播放漏斗层（winmm 直放 / Qt 后端都逃不掉）
    files = {}
    for value, name in (
        ("builtin:agent-start", "start.wav"),
        ("builtin:agent-done", "done.wav"),
        ("builtin:agent-error", "error.wav"),
    ):
        path = tmp_path / name
        path.write_bytes(b"RIFF")
        files[value] = path
    monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda p: files.get(str(p)))
    for fn in ("play_sound", "play_with_winmm", "play_with_qt"):
        monkeypatch.setattr(click_sound._pool, fn,
                            lambda path, volume=1.0: sounds.append(Path(path).name) or True)

    cfg = Config(base=tmp_path / "cfg")
    cfg.data["agent_link"].update({
        "sound_enabled": True,
        "sound_cooldown_seconds": 0.0,
        "sound_start_path": "builtin:agent-start",
        "sound_done_path": "builtin:agent-done",
        "sound_error_path": "builtin:agent-error",
    })
    cfg.save()
    rig = _Rig(mgr=None, win=None, bubbles=bubbles, sounds=sounds,
               switched=switched, redirected=redirected, app=app)
    rig.mgr = AgentLinkManager(DummyWin(), cfg, min_interval=0.0,
                               clock=lambda: rig.clock[0])
    rig.win = rig.mgr.win
    # 白盒：等效 agent_link.dsh 已启用——收敛管线的迟到信号隔离闸只放行
    # 「监视器在跑」的信号，手动驱动 _poll 的测试必须置真。
    rig.mon._running = True
    rig.mon.events_dir.mkdir(parents=True, exist_ok=True)
    return rig


def _write(bridge: Path, name: str, *records) -> None:
    with (bridge / name).open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _bump_dir_mtime(directory: Path) -> None:
    """显式推进目录 mtime，确定性制造「目录变化」信号（解锁 DirGlobTailer 扫描节流）。

    Windows 目录时间戳惰性更新，「建文件 → 目录 mtime 变」不是确定前提——与
    ``test_agent_link.py::TestDirGlobTailer::test_discovers_new_files_during_scan_throttle_and_keeps_offsets``
    同款做法。
    """
    stat = directory.stat()
    os.utime(directory, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _pump(app, seconds: float) -> None:
    """跑满一段事件循环（负向断言专用：给不该存在的定时器犯错的机会）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _wait_until(app, predicate, timeout: float = 5.0) -> bool:
    """宽预算轮询等待（禁止固定 sleep 猜时序：800ms 确认窗在慢 runner 上会漂）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _succeeded(bubbles) -> list:
    return [b for b in bubbles if any(w in b for w in _SUCCEED_WORDS)]


# 回合进行中就已落盘的历史记录：回填防护会把它们整批跳到末尾。
# 三条都不带 state 字段——legacy 分派（normalize_event_state）不认识它们，
# 只有 dsh_state 收敛器认（turn/start→thinking、tool/call→working）。
_IN_FLIGHT_HISTORY = (
    {"event": "turn/start", "sessionId": "s-1"},
    {"event": "assistant/message", "sessionId": "s-1"},
    {"event": "tool/call", "sessionId": "s-1", "tool": "bash"},
)


# ============================================================================
# 前提：中途挂载后，呈现管线确实从没见过这个 Agent（prev 真的是 None）
# ============================================================================
def test_mid_attach_premise_pipeline_has_never_seen_the_agent(tmp_path, monkeypatch):
    """钉住修复赖以成立的生产前提：中途挂载不注入任何状态。

    探活基线 ``idle`` 会经 ``dsh_state_changed`` 走岛侧，但**不**注入呈现管线
    （``_on_dsh_converged_state`` 只注入 thinking/success/offline/审批），所以
    ``_last_raw`` 里没有 dsh 键——这正是 ``prev_raw is None`` 能命中的原因。
    前提若被将来的改动推翻（例如 idle 也注入管线），本用例必须红：那时
    ``prev_raw is None`` 的半边就不再够用。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        rig.mon._probe_online()                 # 在线基线 → 收敛器 idle
        assert rig.mon._converger.current_state is DshState.IDLE
        rig.mon._poll()                         # 首次读：回填跳到末尾，历史整批跳过
        assert "dsh" not in rig.mgr._last_raw, (
            f"中途挂载不得给呈现管线写任何状态，实际 {rig.mgr._last_raw!r}")
        assert "dsh" not in rig.mgr._done_pending
        assert rig.bubbles == [] and rig.sounds == []
    finally:
        rig.mgr.shutdown()


# ============================================================================
# 主回归：回填跳过 turn/start，之后 append 的 turn/end 必须恰好提醒一次
# ============================================================================
def test_skipped_history_then_live_turn_end_notifies_exactly_once(tmp_path, monkeypatch):
    """启动前已在盘上的回合历史被跳过；之后 live 到来的 ``turn/end`` 出一次完成提醒。

    这是 issue #234 残留子集的正面复现：``prev_raw is None`` 也必须进完成边沿。
    修复前 ``_done_pending`` 恒空、气泡与 done 音效都不出现（本用例红）。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        assert mgr._last_raw.get("dsh") is None, \
            f"中途挂载前提：prev 必须是 None，实际 {mgr._last_raw!r}"

        # 回合继续（这些记录也是收敛器独占的），随后 live 收尾
        _write(rig.bridge, "dsh.jsonl",
               {"event": "tool/result", "sessionId": "s-1", "tool": "bash", "ok": True},
               {"event": "step/start", "sessionId": "s-1"})
        mon._poll()
        assert mgr._last_raw.get("dsh") is None, "收敛器独占的 working 不注入呈现管线"
        _write(rig.bridge, "dsh.jsonl", {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()

        assert mon._converger.current_state is DshState.SUCCESS
        assert "dsh" in mgr._done_pending, "中途挂载的 turn/end 必须排定完成确认"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)

        # 恰一次：确认到期后再来两拍轮询（无新记录）不得二次弹/二次响
        mon._poll()
        mon._poll()
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_worker_thread_poll_reaches_manager_through_queued_signals(tmp_path, monkeypatch):
    """同一条链路在**真 worker 线程**里驱动：信号经 Qt 队列投到主线程。

    生产里 ``_poll`` 跑在监视器的 worker 线程，收敛信号与 legacy 信号都是跨线程
    队列投递（顺序 = 发出顺序）。本用例把 ``_poll`` 放进子线程、主线程泵事件循环，
    钉住「中途挂载 → live turn/end」这条链在真实投递路径上同样只提醒一次。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr

    def poll_from_worker():
        thread = threading.Thread(target=mon._poll, name="dsh-midattach-poll", daemon=True)
        thread.start()
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "驱动 poll 的子线程必须及时收工"

    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        poll_from_worker()
        _pump(rig.app, 0.2)                     # 队列投递：泵一次事件即可送达
        assert "dsh" not in mgr._last_raw, mgr._last_raw
        assert "dsh" not in mgr._done_pending

        _write(rig.bridge, "dsh.jsonl", {"event": "turn/end", "sessionId": "s-1"})
        poll_from_worker()
        assert _wait_until(rig.app, lambda: "dsh" in mgr._done_pending), mgr._done_pending
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_attach_history_that_already_ended_is_not_replayed(tmp_path, monkeypatch):
    """启动前就已收尾的回合（历史里带 ``turn/end``）：回填整批跳过，不得重播成功。

    与上一条同一条路径的**反向**：区分「启动前的 history」与「接上之后的 live
    结束」。整条历史连 ``turn/end`` 都在盘上时，收敛器根本不该看到它。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl",
               *_IN_FLIGHT_HISTORY,
               {"event": "turn/end", "sessionId": "s-1"})
        mon._probe_online()
        mon._poll()
        assert mon._converger.current_state is DshState.IDLE, \
            "已收尾的历史不得进入收敛器（否则会重播一次 success）"
        assert "dsh" not in mgr._last_raw
        assert "dsh" not in mgr._done_pending
        _pump(rig.app, 1.2)                     # 超过 800ms 确认窗
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert rig.sounds == [], rig.sounds


# ============================================================================
# 新数据源首次被发现（新 dsh-{pid}.jsonl）：history 不重播，live 结束要提醒
# ============================================================================
def test_new_source_discovered_mid_turn_notifies_on_live_turn_end(tmp_path, monkeypatch):
    """新桥文件在回合进行中被发现：其历史被回填跳过，随后的 live ``turn/end`` 提醒一次。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        mon._probe_online()
        mon._poll()                             # 空目录：先落下扫描基线
        _write(rig.bridge, "dsh-4242.jsonl", *_IN_FLIGHT_HISTORY)
        _bump_dir_mtime(rig.bridge)
        mon._poll()                             # 发现新文件 + 回填跳到末尾
        assert mon._converger.current_state is DshState.IDLE, "新文件的历史不得重播"
        assert "dsh" not in mgr._last_raw
        assert "dsh" not in mgr._done_pending

        _write(rig.bridge, "dsh-4242.jsonl", {"event": "turn/end", "sessionId": "s-9"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS
        assert "dsh" in mgr._done_pending, "新数据源的 live 结束必须提醒"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_new_source_completed_history_not_replayed_but_next_live_turn_is(
        tmp_path, monkeypatch):
    """新文件首次被发现时已是「整回合完成」：历史不重播；同文件的下一个 live 回合照常提醒。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh-7777.jsonl",
               *_IN_FLIGHT_HISTORY,
               {"event": "turn/end", "sessionId": "s-7"})
        _bump_dir_mtime(rig.bridge)
        mon._poll()
        assert mon._converger.current_state is DshState.IDLE, "早已完成的 end 不得重播"
        assert "dsh" not in mgr._done_pending
        _pump(rig.app, 1.2)
        assert _succeeded(rig.bubbles) == [], rig.bubbles

        _write(rig.bridge, "dsh-7777.jsonl",
               {"event": "turn/start", "sessionId": "s-8"},
               {"event": "tool/call", "sessionId": "s-8", "tool": "bash"},
               {"event": "turn/end", "sessionId": "s-8"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS
        assert "dsh" in mgr._done_pending, "同一文件接上后的新回合必须提醒"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_empty_new_source_first_live_record_is_turn_end(tmp_path, monkeypatch):
    """空的新数据源被发现后，第一条 live 记录就是 ``turn/end`` → 提醒一次。

    桥实例在回合中途起来时，``turn/start`` 可能落在别的（老）文件里，新文件的
    第一条记录直接是 ``turn/end``——「此前未识别状态的数据源只收到 success」。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        mon._probe_online()
        mon._poll()
        (rig.bridge / "dsh-9090.jsonl").touch()
        _bump_dir_mtime(rig.bridge)
        mon._poll()                             # 发现空文件：无记录可读
        assert "dsh" not in mgr._last_raw
        assert "dsh" not in mgr._done_pending

        _write(rig.bridge, "dsh-9090.jsonl", {"event": "turn/end", "sessionId": "s-10"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS
        assert "dsh" in mgr._done_pending
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


# ============================================================================
# 重复 success / 硬失败 / 多 Agent / 隐藏改道：既有口径在 None 来向上不得走样
# ============================================================================
def test_duplicate_success_frames_still_notify_once(tmp_path, monkeypatch):
    """中途挂载后重复到达的 success 帧（同一条 ``turn/end`` 再来两遍）只提醒一次。

    ``prev_raw is None`` 的放宽必须仍然自闭合：第一次之后 ``_last_raw`` 已是
    "success"，既不是 busy 也不是 None；收敛器本身也是边沿触发。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl", {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()
        assert "dsh" in mgr._done_pending
        first_timer = mgr._done_pending["dsh"]

        # 收敛器边沿触发：同状态重复记录不产出
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1"},
               {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()
        assert list(mgr._done_pending) == ["dsh"], mgr._done_pending
        assert mgr._done_pending["dsh"] is first_timer, "重复 success 不得重排确认"
        # 直投重复帧（等价于呈现层连收两次 success）同样不重排
        mgr._on_agent_state("dsh", "success")
        mgr._on_agent_state("dsh", "success")
        assert mgr._done_pending["dsh"] is first_timer

        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_mid_attach_hard_failure_never_claims_success(tmp_path, monkeypatch):
    """中途挂载的硬失败回合：``prev`` 为 None 时 ``_saw_error`` 闸必须照旧生效。

    桥接判定失败后先写 ``execution/failed``、随后仍无条件转发 ``turn/end``，
    收敛器一律收敛 success。放宽 busy 前置**不得**把这道闸一起放宽——失败回合
    既不能弹「已完成本轮任务」，也不能播 done 音效。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        assert mgr._last_raw.get("dsh") is None

        _write(rig.bridge, "dsh.jsonl",
               {"event": "tool/result", "sessionId": "s-1", "tool": "bash",
                "ok": False, "errorCode": "E_BASH", "errorMessage": "exit status 1"},
               {"event": "execution/failed", "sessionId": "s-1",
                "failureType": "tool_failed", "errorCode": "E_BASH",
                "errorMessage": "exit status 1"},
               {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS, \
            "收敛器语义不动（turn/end 仍收敛 success，抑制发生在呈现侧）"
        assert "dsh" in mgr._saw_error, "execution/failed 必须按 agent 打标"
        assert "dsh" not in mgr._done_pending, "硬失败回合不得排定完成确认"
        assert rig.bubbles, "失败提醒本身照常弹出"
        _pump(rig.app, 1.2)                     # 超过 800ms 确认窗
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert "done.wav" not in rig.sounds, rig.sounds


def test_error_turn_end_injects_error_into_presentation(tmp_path, monkeypatch):
    """F4 呈现侧：error 收尾必须注入呈现管线——桌宠撤忙碌动画（_last_raw 落 error）、
    按失败口径收尾，而不是停在 thinking 直到下一回合。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _pump(rig.app, 0.2)
        _write(rig.bridge, "dsh.jsonl",
               {"event": "tool/result", "sessionId": "s-1", "tool": "bash", "ok": True},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:7",
                "sourceTurn": "7", "reasonKind": "error"})
        mon._poll()
        _pump(rig.app, 0.2)
        assert mgr._last_raw.get("dsh") == "error", mgr._last_raw
    finally:
        mgr.shutdown()


def test_mid_attach_success_while_other_agent_busy(tmp_path, monkeypatch):
    """多 Agent 并行：dsh 中途挂载收尾时 claude 仍在忙——提醒照出、动画不互顶。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        mgr._on_agent_state("claude", "working")
        rig.switched.clear()
        rig.bubbles.clear()
        rig.sounds.clear()

        _write(rig.bridge, "dsh.jsonl", {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()
        assert "dsh" in mgr._done_pending
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        assert not any(s.startswith("idle:") for s in rig.switched), \
            f"dsh 收尾不得把仍在忙的 claude 顶回待机：{rig.switched}"
        assert rig.sounds.count("done.wav") == 1, rig.sounds

        # claude 自己的收尾不受影响（按 Agent 分开的确认键）
        rig.bubbles.clear()
        rig.clock[0] += 3.0
        mgr._on_agent_state("claude", "idle")
        assert "claude" in mgr._done_pending
        for timer in list(mgr._done_pending.values()):
            timer.timeout.emit()
        _pump(rig.app, 0.3)
        assert _succeeded(rig.bubbles), rig.bubbles
    finally:
        mgr.shutdown()


def test_mid_attach_success_when_hidden_redirects_to_island(tmp_path, monkeypatch):
    """隐藏态中途挂载收尾：气泡改道岛反馈面，不弹直接气泡、不播 done 音效。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        rig.win.isVisible = lambda: False
        rig.win.hidden_bubble_redirect = (
            lambda text, subtitle="", duration_ms=3200:
            (rig.redirected.append((str(text), duration_ms)), True)[1])
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        assert mon._converger.current_state is DshState.IDLE
        rig.sounds.clear()

        _write(rig.bridge, "dsh.jsonl", {"event": "turn/end", "sessionId": "s-1"})
        mon._poll()
        assert "dsh" in mgr._done_pending
        assert _wait_until(rig.app, lambda: bool(rig.redirected)), rig.redirected
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    texts = [t for t, _ in rig.redirected]
    assert any(any(w in t for w in _SUCCEED_WORDS) or "干完活" in t for t in texts), texts
    assert rig.bubbles == [], rig.bubbles
    assert rig.sounds == [], rig.sounds


# ============================================================================
# ② live 事件身份判据：不让任何「管线里的上一个状态」吞掉真实收尾
# ============================================================================
#
# 上面的用例证明了 prev=None 的那条来向；下面把「该不该提醒」的判据从
# 「上一个状态是什么」整体交给 **live 记录身份**（session + step + 本会话 live
# 回合序号）——于是下面四种来向一次收口，不再逐个补状态特判：
#   ① 中途挂载（prev=None，聚合 idle 基线还没到）        → test 1（上面）
#   ② 聚合基线 idle 先到（prev="idle"）                  → R1
#   ③ 收敛器已停在 SUCCESS（无状态转换，如新会话新文件）  → R2 / R6
#   ④ 关掉再开启联动留下的终态残留（prev="success"）      → R3
# 同时钉住身份判据的两个反面：同一回合的重复投递只提醒一次（R4），
# 同一会话的连续两个回合、两个会话的同 step 回合都各提醒一次（R5 / R6）。


def test_live_idle_baseline_before_turn_end_still_notifies_once(tmp_path, monkeypatch):
    """R1：聚合基线 ``AgentStatus{idle}`` 先到（prev 变 "idle"）→ live 收尾仍提醒一次。

    桥接的 AgentStatus 聚合基线与 session/event 是两条通道，idle 可能先落地
    （既有用例 ⑩ 已钉住这条真实顺序）。prev 既不是 busy 也不是 None，
    ``busy→idle`` 也不会排定（来向不是 busy）——只能由 live 记录身份判定。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl", {"event": "AgentStatus", "state": "idle"})
        mon._poll()
        assert mgr._last_raw.get("dsh") == "idle", mgr._last_raw
        assert "dsh" not in mgr._done_pending, "idle 基线本身不是收尾（来向不是 busy）"
        rig.bubbles.clear()
        rig.sounds.clear()

        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._done_pending, "live turn/end 必须排定完成确认（不认 prev）"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_live_turn_end_notifies_when_converger_stays_success(tmp_path, monkeypatch):
    """R2：收敛器已停在 SUCCESS（同状态零产出）时，新数据源的 live 收尾仍提醒。

    回合 A 正常收尾后收敛器停在 SUCCESS；回合 B 走**新桥文件**，其历史被回填
    跳过，live 只来了一条 ``turn/end``——收敛器 SUCCESS→SUCCESS 不产出，
    呈现侧拿不到任何 success；判定只能落在 live 记录身份上。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-a", "step": "turn:1"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
        rig.clock[0] += 10.0                    # 越过完成冷却（同 Agent 5s）

        # 回合 B：新文件在回合进行中被发现（历史跳过），live 只有收尾
        _write(rig.bridge, "dsh-5555.jsonl", *_IN_FLIGHT_HISTORY)
        _bump_dir_mtime(rig.bridge)
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS, "会话 A 的状态不得被新文件顶掉"
        assert "dsh" not in mgr._done_pending

        _write(rig.bridge, "dsh-5555.jsonl",
               {"event": "turn/end", "sessionId": "s-b", "step": "turn:1"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS, "同状态：收敛器零产出（前提）"
        assert "dsh" in mgr._done_pending, "新会话的 live 收尾必须提醒"
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_link_off_on_residue_does_not_swallow_live_turn_end(tmp_path, monkeypatch):
    """R3：关掉再开启联动（真 start/stop + 真 worker）后，残留终态不得吞掉 live 收尾。

    关闭联动会经 offline 复位管线清掉原始状态镜像（``_last_raw`` 不再残留上一轮的
    终态，"新 DSH 重启不永远忙"）。本用例证明的是另一件事：**即便呈现管线里没有
    任何可用的 prev**（复位后 prev 是 None、回填又把这一回合的历史整批跳过），
    live 回合收尾仍然照常提醒一次——判定交给 live 记录身份，不依赖 prev。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon.start()                             # 真 worker 线程
        assert _wait_until(rig.app, lambda: bool(mon._tailer._tailers), timeout=30.0), \
            "worker 第一拍必须完成扫描（回填跳过启动前的历史）"
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles), timeout=30.0), rig.bubbles
        assert mgr._last_raw.get("dsh") == "success", mgr._last_raw

        # 关闭联动（真 stop + 既有复位管线）：写者随通道一起消失，原始状态镜像整片
        # 作废——否则旧实例留下的终态/忙态会跨代次存活（关掉再开一直是"忙"）。
        mon.stop()
        mgr._on_dsh_converged_state("", "offline", "link-disabled")
        assert mgr._last_raw.get("dsh") is None, "offline 必须清掉原始状态镜像"
        assert not mgr._owner_raw, "offline 必须清掉写者分片"
        rig.clock[0] += 10.0                    # 越过完成冷却

        # 关闭期间这一回合的历史已经落盘；重新开启后回填跳过它，live 只来收尾
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon.start()
        assert _wait_until(rig.app, lambda: bool(mon._tailer._tailers), timeout=30.0)
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-2", "step": "turn:1"})
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2, timeout=30.0), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_same_turn_end_written_to_two_files_notifies_once(tmp_path, monkeypatch):
    """R4：同一回合的收尾被两个写者各写一份（两个文件、同 session 同 step）→ 只提醒一次。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        mon._probe_online()
        (rig.bridge / "dsh-11.jsonl").touch()
        (rig.bridge / "dsh-22.jsonl").touch()
        _bump_dir_mtime(rig.bridge)
        mon._poll()                             # 两个空文件都被发现
        assert len(mon._tailer._tailers) == 2, list(mon._tailer._tailers)

        end = {"event": "turn/end", "sessionId": "s-1", "step": "turn:7"}
        _write(rig.bridge, "dsh-11.jsonl", end)
        _write(rig.bridge, "dsh-22.jsonl", end)
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.5)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


# 回合信封的**真实形状**：真实 DSH 的 ``UserMessage`` 是 ``{id, role, content, source}``
# ——没有 ``turn`` 字段（真实桥日志 213 条 user/message 全部无 step/turn），因此桥接
# 写出的 user/message 既不带 step、也不带 sourceTurn，而且每回合有多条（真人 1 条 +
# agent.inject() 注入的若干条上下文）。回合号只有 turn/start / turn/end 带。
_REAL_ENVELOPE = (
    {"event": "user/message", "sessionId": "s-1", "sourceKind": "user"},
    {"event": "user/message", "sessionId": "s-1", "sourceKind": "agent-instructions"},
    {"event": "user/message", "sessionId": "s-1", "sourceKind": "skill-catalog"},
    {"event": "user/message", "sessionId": "s-1", "sourceKind": "skill-invocation"},
    {"event": "turn/start", "sessionId": "s-1", "step": "turn:7", "sourceTurn": "7"},
    {"event": "turn/end", "sessionId": "s-1", "step": "turn:7", "sourceTurn": "7"},
)


def test_mirrored_envelope_with_user_message_notifies_once(tmp_path, monkeypatch):
    """X1：两个写者各写**一整封真实形状的镜像信封** → 只提醒一次。

    旧判据假定 user/message 带 ``sourceTurn``（按回合号绑身份），真实记录不带——于是
    每条 user/message 都重新发号：第二个写者的信封一进来，同一回合的 token 就被顶到
    新值上，两份 turn/end 算出两个身份，消费侧身份去重失效。本用例的 A 信封 → 越过
    完成冷却 → B 信封序列正是复发路径：第二次完成提醒照弹（本用例红）。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    envelope = _REAL_ENVELOPE
    try:
        mon._probe_online()
        (rig.bridge / "dsh-11.jsonl").touch()
        (rig.bridge / "dsh-22.jsonl").touch()
        _bump_dir_mtime(rig.bridge)
        mon._poll()                             # 两个空文件都被发现
        assert len(mon._tailer._tailers) == 2, list(mon._tailer._tailers)

        _write(rig.bridge, "dsh-11.jsonl", *[{**r, "inst": "bootA"} for r in envelope])
        mon._poll()
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
        rig.clock[0] += 10.0                    # 越过完成冷却：真排定的第二次确认会弹

        _write(rig.bridge, "dsh-22.jsonl", *[{**r, "inst": "bootB"} for r in envelope])
        mon._poll()
        _pump(rig.app, 1.5)                     # 给不该存在的第二次确认留出整个确认窗
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_error_turn_end_never_notifies_success(tmp_path, monkeypatch):
    """F4：``reasonKind=error`` 的回合收尾不得报成功（无完成气泡、无 done 音效）。

    桥端只在「重试耗尽」或「本轮工具失败且无成功」时写 execution/failed；重试 <4 或
    本轮有工具成功时**不写任何抑制标记**，此前这种失败回合照样按 turn/end → success
    收敛，绿点 + done 音效 + 「干完活啦」一起冒出来。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    converged: list[tuple[str, str]] = []
    mgr.dsh_state_changed.connect(lambda _f, to_state: converged.append(to_state))
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "tool/result", "sessionId": "s-1", "tool": "bash", "ok": True},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:7",
                "sourceTurn": "7", "reasonKind": "error"})
        mon._poll()
        assert mon._converger.current_state is DshState.ERROR, \
            "reasonKind=error 的收尾必须落错误终态，不得收敛 success"
        assert "error" in converged, f"错误终态必须转发到灵动岛：{converged}"
        assert "success" not in converged, converged
        assert "dsh" not in mgr._done_pending, "错误回合不得排定完成确认"
        _pump(rig.app, 1.2)                     # 超过 800ms 确认窗
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert "done.wav" not in rig.sounds, rig.sounds


def test_next_turn_on_another_writer_with_new_source_turn_notifies_again(
        tmp_path, monkeypatch):
    """另一回合在另一个写者上继续（回合号不同）→ 两次完成都要提醒，绝不吞。

    与上一个用例成对：镜像绑定只看**回合号**，不看写者归属。同一 (会话, step) 上另一
    个回合号就是另一回合——「别的写者的 user/message 一律算镜像」会把这次完成整条吞掉
    （漏报完成，动画/音效都不会有）。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    first_env = (
        {"event": "user/message", "sessionId": "s-1", "step": "turn:7", "sourceTurn": "7"},
        {"event": "turn/start", "sessionId": "s-1", "step": "turn:7", "sourceTurn": "7"},
        {"event": "turn/end", "sessionId": "s-1", "step": "turn:7", "sourceTurn": "7"},
    )
    # 下一回合：步长沿用同值（镜像绑定的诱饵），只有回合号变了
    next_env = tuple({**r, "sourceTurn": "8"} for r in first_env)
    try:
        mon._probe_online()
        (rig.bridge / "dsh-11.jsonl").touch()
        (rig.bridge / "dsh-22.jsonl").touch()
        _bump_dir_mtime(rig.bridge)
        mon._poll()
        assert len(mon._tailer._tailers) == 2, list(mon._tailer._tailers)

        _write(rig.bridge, "dsh-11.jsonl", *[{**r, "inst": "bootA"} for r in first_env])
        mon._poll()
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        rig.clock[0] += 10.0                    # 越过完成冷却

        _write(rig.bridge, "dsh-22.jsonl", *[{**r, "inst": "bootB"} for r in next_env])
        mon._poll()
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_successive_turns_in_one_session_each_notify_once(tmp_path, monkeypatch):
    """R5：同一会话连续两个回合（live 起点区分）→ 两个回合各提醒一次，不误判为重复。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        rig.clock[0] += 10.0

        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/start", "sessionId": "s-1", "step": "turn:2"},
               {"event": "tool/call", "sessionId": "s-1", "tool": "bash"},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:2"})
        mon._poll()
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_two_sessions_with_same_step_each_notify_once(tmp_path, monkeypatch):
    """R6：两个会话的收尾 step 值相同 → 身份含会话，两次都提醒（且收敛器无转换）。

    这是「身份 = session + step + live 回合序号」里 session 那一项的守门用例：
    少了 session，第二次收尾会被误当重复吞掉。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        rig.clock[0] += 10.0

        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-2", "step": "turn:1"})
        mon._poll()
        assert mon._converger.current_state is DshState.SUCCESS, "同状态：无转换（前提）"
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_evicted_session_same_step_next_turn_notifies_again(tmp_path, monkeypatch):
    """会话表驱逐后，同会话同 step 的新回合仍提醒一次（token 不得重用）。

    ``TurnCompletionTracker`` 的会话表有界（``dsh_state.TURN_TOKEN_MEMORY``），长跑
    进程里会话被挤出去是常态。若挤出去后 token 从头重来，同一会话、同 step 的下一回合
    就会拿到与上一回合**相同**的身份，而 manager 的身份记忆（``_dsh_turn_ids``）按身份
    去重、且不随监视器生命周期清空——新回合会被当成重复投递静默吞掉（无气泡、无 done
    音效）。本用例把这条路径走真链路：真 tailer → 真 ``_poll`` → 真收敛器 → 真身份
    通道 → 真 800ms 确认窗。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()

        # 回合 A：s-1 一整轮（live 起点 + 收尾）→ 恰好一次提醒
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/start", "sessionId": "s-1", "step": "turn:1"},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
        assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
        rig.clock[0] += 10.0                    # 越过完成冷却（同 Agent 5s）

        # 灌入远超容量的 live 起点，把 s-1 挤出会话表
        _write(rig.bridge, "dsh.jsonl", *[
            {"event": "turn/start", "sessionId": f"flood-{i}", "step": "turn:1"}
            for i in range(dsh_state.TURN_TOKEN_MEMORY + 16)
        ])
        mon._poll()
        assert "s-1" not in mon._turn_tracker._tokens, "前提：s-1 必须真的被容量挤出"

        # 回合 B：同一会话、同 step 的新回合——身份不同 → 仍排定一次确认
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/start", "sessionId": "s-1", "step": "turn:1"},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert _wait_until(
            rig.app, lambda: len(_succeeded(rig.bubbles)) == 2), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 2, rig.bubbles
    assert rig.sounds.count("done.wav") == 2, rig.sounds


def test_hard_failure_suppresses_completion_after_idle_baseline(tmp_path, monkeypatch):
    """R7：硬失败闸在「idle 基线先到」来向上同样生效（不得因判据换轨而漏掉）。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "AgentStatus", "state": "idle"},
               {"event": "execution/failed", "sessionId": "s-1",
                "failureType": "tool_failed", "errorCode": "E_BASH",
                "errorMessage": "exit status 1"},
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._saw_error
        assert "dsh" not in mgr._done_pending, "硬失败回合不得排定完成确认"
        assert rig.bubbles, "失败提醒照常"
        _pump(rig.app, 1.2)
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert "done.wav" not in rig.sounds, rig.sounds


def test_completion_cancelled_by_live_busy_frame_inside_confirm_window(tmp_path, monkeypatch):
    """R8：确认窗内回忙（live ``AgentStatus{working}``）仍取消完成确认（800ms 去抖口径不变）。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._done_pending
        rig.bubbles.clear()
        rig.sounds.clear()
        _write(rig.bridge, "dsh.jsonl", {"event": "AgentStatus", "state": "working"})
        mon._poll()
        assert "dsh" not in mgr._done_pending, "确认窗内回忙必须取消完成确认"
        _pump(rig.app, 1.2)
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert "done.wav" not in rig.sounds, rig.sounds   # 回忙会正常播 start 音效


def test_dsh_completion_leaves_other_agent_state_alone(tmp_path, monkeypatch):
    """R9：dsh 的 live 收尾不得改动别的 Agent 的原始状态/确认键。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        mgr._on_agent_state("claude", "working")
        before = dict(mgr._last_raw)

        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert mgr._last_raw.get("claude") == before.get("claude") == "working"
        assert "claude" not in mgr._done_pending, "dsh 收尾不得给别的 Agent 排确认"
        assert "claude" not in mgr._saw_error
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles


# ============================================================================
# ③ 监测启停生命周期：在飞的完成确认与上一代的退役簿记
# ============================================================================
#
# L01：live 收尾排定 800ms 确认 → 用户在窗口内关掉 DSH 联动 → 旧回执不该再弹；
#      关闭→立刻重开时，旧 timer 也不得作为新代的回调弹出来（回调绑定创建时代次）。
# L02：上一代的硬失败/原始状态/确认簿记必须随关闭退役，否则新代中途接上的新回合
#      成功收尾会被旧 `_saw_error` 整个吞掉；本代实际摄入的硬失败照旧抑制。
# 全部走真 `apply_config` / 真 `monitor.start|stop` / 真 QTimer / 真嵌套事件循环。


def _make_lifecycle_rig(tmp_path, monkeypatch) -> _Rig:
    """启停用的 rig：真 apply_config / 真 start|stop，只把 worker 主体与桥自检替掉。

    worker 主体替成空实现只是为了不引入后台 1.5s 轮询与本机端口探测——``start()``
    的代次自增、tailer/账本/tracker 复位全部照常执行，而这正是 L01/L02 要验的启停
    机制。``schedule_link_refresh_check`` 会去扫真实 DSH profile（陈旧时跑 pnpm），
    与本任务无关，一并替掉。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_link.DshMonitor, "_work_loop", lambda self, gen: None)
    monkeypatch.setattr(agent_link.DshMonitor, "schedule_link_refresh_check",
                        lambda self, spawn=None: None)
    rig.mgr.cfg.data["agent_link"]["dsh"] = False
    rig.mon._running = False
    return rig


def _toggle_dsh(rig: _Rig, enabled: bool) -> None:
    """走真实配置开关路径（apply_config 启停监视器）。"""
    rig.mgr.cfg.data["agent_link"]["dsh"] = bool(enabled)
    rig.mgr.apply_config()


def _spin(app, ms: int) -> None:
    """跑一段真嵌套事件循环：QTimer 按**真实时间**到期，不靠 sleep 猜时序。"""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_disable_cancels_pending_completion_and_stays_silent(tmp_path, monkeypatch):
    """L01：关闭联动必须取消本 Agent 在飞的完成确认——关掉之后无声无泡。"""
    rig = _make_lifecycle_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _toggle_dsh(rig, True)
        assert mon._running
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._done_pending, "前提：live 收尾已排定 800ms 确认"

        # 跨 source 守门：另一个 Agent 的确认与状态不得被连坐清掉
        mgr._on_agent_state("claude", "working")
        mgr._schedule_done_check("claude")
        claude_raw = mgr._last_raw.get("claude")

        _toggle_dsh(rig, False)
        assert not mon._running
        assert "dsh" not in mgr._done_pending, \
            "关闭联动必须取消本 Agent 在飞的完成确认（否则关掉后仍会弹）"
        assert "claude" in mgr._done_pending, "不得清掉别的 Agent 的确认键"
        assert mgr._last_raw.get("claude") == claude_raw, "不得改别的 Agent 的原始状态"
        mgr._cancel_done_check("claude")             # 别让它在本用例的观察窗里到期

        rig.bubbles.clear()
        rig.sounds.clear()
        _spin(rig.app, 1000)                        # 超过 800ms 确认窗
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert rig.sounds == [], rig.sounds


def test_reopen_does_not_let_old_timer_fire_as_new_generation(tmp_path, monkeypatch):
    """L01 后半：关闭→立刻重开，旧代定时器不得作为新代回调；新代自己照常提醒一次。"""
    rig = _make_lifecycle_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _toggle_dsh(rig, True)
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-old", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._done_pending

        _toggle_dsh(rig, False)
        _toggle_dsh(rig, True)
        assert mon._running
        assert "dsh" not in mgr._done_pending, "重开后不得留着旧代的完成确认"
        rig.bubbles.clear()
        rig.sounds.clear()
        _spin(rig.app, 1000)                        # 旧 timer 若未退役就会在此代到期
        assert _succeeded(rig.bubbles) == [], \
            f"旧代的完成确认不得作为新代回调弹出来：{rig.bubbles}"

        # 新代第一拍：回填跳过盘上已有内容（旧代那条收尾不得重播）
        mon._poll()
        assert "dsh" not in mgr._done_pending, "新代不得重播上一代已投递过的收尾"

        # 新代自己的 live 收尾（起点仍被回填跳过、聚合基线先到）→ 恰一次
        _write(rig.bridge, "dsh.jsonl",
               {"event": "AgentStatus", "state": "idle"},
               {"event": "turn/end", "sessionId": "s-new", "step": "turn:9"})
        mon._poll()
        assert "dsh" in mgr._done_pending, "新代的 live 收尾必须照常排定确认"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_new_generation_midattach_notifies_once_despite_old_hard_failure(
        tmp_path, monkeypatch):
    """L02：上一代的 execution/failed 标记必须随关闭退役，新代中途接上仍提醒一次。"""
    rig = _make_lifecycle_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _toggle_dsh(rig, True)
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "execution/failed", "sessionId": "s-old",
                "failureType": "tool_failed", "errorCode": "E_BASH",
                "errorMessage": "exit status 1"},
               {"event": "turn/end", "sessionId": "s-old", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._saw_error, "前提：旧代硬失败已打标"
        assert "dsh" not in mgr._done_pending
        _spin(rig.app, 1000)
        assert _succeeded(rig.bubbles) == [], "旧代硬失败不得报成功（既有契约）"

        _toggle_dsh(rig, False)
        _toggle_dsh(rig, True)                      # 换监测代
        assert "dsh" not in mgr._saw_error, \
            "上一代的硬失败标记必须随关闭退役（否则新代所有会话都被吞）"
        assert "dsh" not in mgr._last_raw, "上一代的原始状态不得留给新代"

        mon._poll()                                     # 新代第一拍：回填跳过盘上旧内容
        assert "dsh" not in mgr._done_pending, "新代不得重播上一代的收尾"
        rig.bubbles.clear()
        rig.sounds.clear()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "AgentStatus", "state": "idle"},      # 聚合基线先到
               {"event": "turn/end", "sessionId": "s-new", "step": "turn:2"})
        mon._poll()
        assert "dsh" in mgr._done_pending, "新代的 live 收尾必须排定确认"
        assert _wait_until(rig.app, lambda: _succeeded(rig.bubbles)), rig.bubbles
        _pump(rig.app, 0.3)
    finally:
        mgr.shutdown()
    assert len(_succeeded(rig.bubbles)) == 1, rig.bubbles
    assert rig.sounds.count("done.wav") == 1, rig.sounds


def test_new_generation_hard_failure_is_still_suppressed(tmp_path, monkeypatch):
    """L02 反面：退役不得把硬失败闸一起退役——新代实际摄入的失败仍不报成功。"""
    rig = _make_lifecycle_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _toggle_dsh(rig, True)
        _toggle_dsh(rig, False)
        _toggle_dsh(rig, True)                      # 新代（簿记刚被退役过）
        assert "dsh" not in mgr._saw_error

        # 新代的桥文件在回合进行中才被发现：历史被回填跳过，失败记录随后 live 到来
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "execution/failed", "sessionId": "s-new",
                "failureType": "tool_failed", "errorCode": "E_BASH",
                "errorMessage": "exit status 1"},
               {"event": "turn/end", "sessionId": "s-new", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._saw_error, "本代的 execution/failed 必须照旧打标"
        assert "dsh" not in mgr._done_pending, "本代硬失败回合不得排定完成确认"
        rig.bubbles.clear()
        rig.sounds.clear()
        _spin(rig.app, 1000)
    finally:
        mgr.shutdown()
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert "done.wav" not in rig.sounds, rig.sounds


def test_stale_timer_cannot_fire_when_monitor_restarts_without_config_path(
        tmp_path, monkeypatch):
    """L01 兜底：不走 apply_config 的直接 stop→start，创建时绑定的代次也要挡住旧回调。"""
    rig = _make_rig(tmp_path, monkeypatch)
    mon, mgr = rig.mon, rig.mgr
    try:
        _write(rig.bridge, "dsh.jsonl", *_IN_FLIGHT_HISTORY)
        mon._probe_online()
        mon._poll()
        _write(rig.bridge, "dsh.jsonl",
               {"event": "turn/end", "sessionId": "s-1", "step": "turn:1"})
        mon._poll()
        assert "dsh" in mgr._done_pending
        gen_before = mon._emit_gen

        mon.stop()                                  # 直接停：不经过 apply_config 的退役
        monkeypatch.setattr(agent_link.DshMonitor, "_work_loop", lambda self, gen: None)
        assert mon.start()
        assert mon._emit_gen != gen_before, "重开必须换代次（前提）"
        assert "dsh" in mgr._done_pending, "直接停不会退役，旧确认仍在飞（本用例的前提）"

        rig.bubbles.clear()
        rig.sounds.clear()
        _spin(rig.app, 1000)                        # 旧 timer 到点：代次不匹配必须挡掉
    finally:
        mgr.shutdown()
    assert "dsh" not in mgr._done_pending, "定时器必须真的到期并被回收（否则本用例空转）"
    assert _succeeded(rig.bubbles) == [], rig.bubbles
    assert rig.sounds == [], rig.sounds


@pytest.mark.parametrize("pattern", ["dsh.jsonl", "dsh-1234.jsonl"])
def test_backfill_is_not_disabled_by_the_test_harness(pattern, tmp_path, monkeypatch):
    """守门：本模块的链路必须真的开着回填（否则上面的用例会退化成白盒直投）。

    真 ``DirGlobTailer`` 在默认 scan_interval 下也允许显式推进目录 mtime 立刻
    发现新文件；这里钉住的是「未读过任何内容时不产出历史行」这一前提。
    """
    rig = _make_rig(tmp_path, monkeypatch)
    try:
        _write(rig.bridge, pattern, {"event": "turn/end", "sessionId": "s-1"})
        assert rig.mon._tailer.read_new_lines() == [], \
            "回填必须保持开启：新 tailer 的首次读取只能空手而归"
    finally:
        rig.mgr.shutdown()
