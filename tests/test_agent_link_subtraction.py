# -*- coding: utf-8 -*-
"""DSH 联动线减法（2026-10）回归测试。

钉住三条减法契约：
1. **单读方**：DshMonitor 是桥目录唯一读方——一份 jsonl 经一次 ``_poll`` 解析，
   同时驱动「信号分派」（state/activity/raw_record 信号）与「状态机」
   （dsh_state 收敛器的 dsh_state_changed/dsh_user_message 信号）两个消费者；
   端口探活并入同一线程（dsh_state.py 不再含 Qt/线程）。
2. **音效开关**：``agent_link.sound_enabled=false`` 时联动域零声音——
   钉在播放漏斗（ClickSoundPool）层，winmm 直放/Qt 后端任何路径都逃不掉。
3. **桥接脱敏**：index.js 不再落明文 text/command/argsKey/resultSummary 字段，
   mux/控制队列/看门狗标识符零残留（与 verify_import.mjs 同口径，进 pytest 套件）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet import agent_link, harness_launcher
from pet.agent_link import AgentLinkManager, DshMonitor
from pet.config import Config
from pet.agent_link.dsh_state import DshState

REPO_ROOT = Path(__file__).resolve().parent.parent


def _qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 1. 单读方
def _make_dsh_monitor(tmp_path, monkeypatch, online=True):
    """构造指向临时桥目录的 DshMonitor（worker 不真起，手动驱动 _poll）。"""
    _qapp()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True)
    bridge_dir = tmp_path / "dsh-pet-bridge"
    bridge_dir.mkdir(parents=True)
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: online)
    # 「桌面端进程在跑」是 online 的旁路判定（机器相关 OS 边界）：本文件用端口
    # 探测表达在线/离线，必须钉死旁路——否则本机装了并在跑桌面端时，
    # online=False 形同虚设，离线分支根本进不去。
    monkeypatch.setattr(harness_launcher, "desktop_process_running", lambda: False)
    mon = DshMonitor("dsh", config_dir)
    assert mon.events_dir == bridge_dir
    mon._tailer._initial_backfill_done = True  # 跳过 backfill 防护，立即读已写入内容
    return mon, bridge_dir


def _write(bridge_dir, *records):
    with (bridge_dir / "dsh.jsonl").open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_single_reader_feeds_state_machine_and_signal_dispatch(tmp_path, monkeypatch):
    """同一份 jsonl、一次 _poll：信号分派与 8 态收敛器都被驱动。"""
    mon, bridge_dir = _make_dsh_monitor(tmp_path, monkeypatch, online=True)
    try:
        states, tools, converged, messages = [], [], [], []
        mon.state_event.connect(lambda ev: states.append(ev.state))
        mon.activity_event.connect(lambda ev: tools.append(ev.tool))
        mon.dsh_state_changed.connect(lambda f, t: converged.append(t))
        mon.dsh_user_message.connect(lambda sid, text: messages.append(sid))

        mon._probe_online()  # 在线基线 → idle（收敛器首个产出）
        _write(bridge_dir,
               {"event": "user/message", "sessionId": "s-1", "sourceKind": "user"},
               {"event": "tool/call", "tool": "bash", "callId": "c-1"},
               {"event": "AgentStatus", "state": "working"})
        mon._poll()

        # 信号分派消费到：tool/call 的 activity + AgentStatus 的 working 状态
        assert "bash" in tools, "信号分派未消费 tool/call"
        assert "working" in states, "信号分派未消费 AgentStatus.working"
        # 收敛器消费到：idle 基线 → thinking（真人消息）→ working（tool/call）
        assert converged[:1] == ["idle"], "探活基线未落下 idle"
        assert "thinking" in converged and converged[-1] == "working"
        assert messages == ["s-1"], "真人消息未到达收敛器 user_message 输出"
        assert mon._converger.current_state is DshState.WORKING
    finally:
        mon.stop()


def test_single_reader_offline_gates_converger_but_not_dispatch(tmp_path, monkeypatch):
    """离线时收敛器不消费事件（离线态优先），信号分派照常（与旧 DshMonitor 一致）。"""
    mon, bridge_dir = _make_dsh_monitor(tmp_path, monkeypatch, online=False)
    try:
        states, converged = [], []
        mon.state_event.connect(lambda ev: states.append(ev.state))
        mon.dsh_state_changed.connect(lambda f, t: converged.append(t))

        mon._probe_online()
        assert converged == ["offline"]
        assert mon._converger.current_state is DshState.OFFLINE

        _write(bridge_dir, {"event": "AgentStatus", "state": "working"})
        mon._poll()
        assert "working" in states, "离线时 legacy 信号分派不受影响"
        assert converged == ["offline"], "离线时收敛器不得消费事件"
    finally:
        mon.stop()


def test_dsh_state_module_is_pure_logic():
    """dsh_state.py 不得再含 Qt/线程/轮询：纯函数 + 纯类，由唯一读方调用。

    以 AST 校验（docstring 里介绍历史背景的措辞不误判）：无 Qt import、
    无 QObject 子类、无线程/定时器构造。
    """
    import ast as _ast

    tree = _ast.parse((REPO_ROOT / "pet" / "agent_link" / "dsh_state.py").read_text(encoding="utf-8"))
    imported = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            imported.add(node.module or "")
    assert not any("PySide6" in m or "threading" in m for m in imported), (
        f"dsh_state.py 不得再 import Qt/线程: {imported}")
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef):
            bases = {_ast.unparse(b) for b in node.bases}
            assert not any("QObject" in b for b in bases), "dsh_state.py 不得再有 QObject 子类"
    # 桌面端端口候选钉住（offline 判定的唯一来源）
    assert "19387" in (REPO_ROOT / "pet" / "agent_link" / "dsh_state.py").read_text(encoding="utf-8"), \
        "desktop 桌面端 host 端口 19387 必须在探测候选里"


def test_dsh_monitor_has_single_tailer_no_extra_timers(tmp_path, monkeypatch):
    """DshMonitor：一个 DirGlobTailer + 继承的单个轮询线程，无 QTimer 副轮询。"""
    mon, _ = _make_dsh_monitor(tmp_path, monkeypatch)
    try:
        from PySide6.QtCore import QTimer
        assert not mon.findChildren(QTimer), "单读方不得再有 QTimer 轮询（旧 DshStateTracker 两张表已删）"
    finally:
        mon.stop()


# ---------------------------------------------------------------- 2. 音效开关
class _Win:
    cats = {"acts": ["写代码"], "acts_map": {}}
    idles = ["待机"]
    _bubble_busy_until = 0.0

    def isVisible(self):
        return True

    def mark_activity(self):
        pass

    def request_link_anim(self, name):
        pass

    def request_link_idle(self):
        pass

    def show_bubble(self, *_a, **_kw):
        pass


@pytest.fixture()
def sound_spy(monkeypatch):
    """钉在播放漏斗层：winmm 直放 / Qt 后端 / 系统播放器全部经过这里。"""
    from pet import click_sound
    calls = []
    monkeypatch.setattr(click_sound._pool, "play_sound",
                        lambda path, volume=1.0: calls.append(("pool", path)) or True)
    monkeypatch.setattr(click_sound._pool, "play_with_winmm",
                        lambda path, volume=1.0: calls.append(("winmm", path)) or True)
    monkeypatch.setattr(click_sound._pool, "play_with_qt",
                        lambda path, volume=1.0: calls.append(("qt", path)) or True)
    return calls


def test_link_sounds_silent_when_sound_disabled(tmp_path, monkeypatch, sound_spy):
    """sound_enabled=False：start/done/error 全生命周期零声音（含 winmm 直放路径）。"""
    cfg = Config(base=tmp_path)
    cfg.data["agent_link"].update({
        "sound_enabled": False,
        "sound_start_path": "builtin:agent-start",
        "sound_done_path": "builtin:agent-done",
        "sound_error_path": "builtin:agent-error",
    })
    mgr = AgentLinkManager(_Win(), cfg, min_interval=0.0)
    try:
        mgr._on_agent_state("dsh", "working")     # start 点
        mgr._on_agent_state("dsh", "error")       # error 点
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")                     # done 点
        # 收敛器 attention 路径（waiting_approval 纯提示）也不许响
        mgr._on_dsh_converged_state("working", "waiting_approval")
        assert sound_spy == [], f"sound_enabled=false 时联动域必须零声音，实际: {sound_spy}"
    finally:
        mgr.shutdown()


def test_link_sounds_play_through_funnel_when_enabled(tmp_path, monkeypatch, sound_spy):
    """对照组：sound_enabled=True 且路径有效时，声音确实经过同一漏斗（防假绿）。"""
    cfg = Config(base=tmp_path)
    cfg.data["agent_link"].update({"sound_enabled": True, "sound_cooldown_seconds": 0.0})
    sound = tmp_path / "s.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda _p: sound)
    mgr = AgentLinkManager(_Win(), cfg, min_interval=0.0)
    try:
        mgr._on_agent_state("dsh", "working")
        assert sound_spy, "开关打开时 start 音效必须到达播放漏斗"
    finally:
        mgr.shutdown()


# ---------------------------------------------------------------- 3. 桥接脱敏
def test_bridge_source_has_no_plaintext_or_retired_machinery():
    """index.js 静态闸：明文字段写函数与已删机制标识符零残留。"""
    src = (REPO_ROOT / "integrations" / "dsh-pet-bridge" / "index.js").read_text(encoding="utf-8")
    # 脱敏（#226）：这些字段/函数的明文落盘路径必须不存在
    for banned in ("messageText", "commandFromArgs", "summarizeArgs", "extractCommand",
                   "latestCommandFor", "argsKey", "resultSummary",
                   "evidenceHash", "durationMs"):
        assert not re.search(r"\b" + re.escape(banned) + r"\b", src), (
            f"脱敏红线：index.js 不得再含 {banned}")
    # 退役机制：mux 中继 / 控制队列 / 看门狗转发
    for banned in ("muxConnect", "events.mux", "startControlQueue", "handleControlRequest",
                   "runBridgeDiagnosis", "watchdog-request-", "WATCHDOG_EVENT_TYPES",
                   "session-shape", "rawWorkspace", "rawProject"):  # M1: 临时诊断落盘已删
        assert banned not in src, f"已删机制不得残留: {banned}"
    # user/message 与 assistant/message 记录不得再带 text 字段
    assert 'text: messageText' not in src
    assert "createUserMessage" not in src, "LLM 诊断/steer 已删，envelope 构造器不得残留"


# ---------------------------------------------------------------- 4. 审批收敛 gated 口径（H1/中-1）
class _InteractionWin:
    """审批/问题链路的窗口桩：普通气泡 + 提醒队列双通道记录。"""

    def __init__(self):
        self.bubbles: list[str] = []
        self.alerts: list[dict] = []
        self.resolved: list[str] = []
        self._alert_current = None
        self._alert_queue: list = []
        self._sticky_bubble_active = False
        self._bubble_busy_until = 0.0

    def isVisible(self):
        return True

    def show_bubble(self, text, duration_ms=3200, **_kw):
        self.bubbles.append(str(text))

    def show_alert(self, text, *, alert_id="", sticky=True, duration_ms=0, **_kw):
        self.alerts.append({"text": str(text), "alert_id": alert_id, "sticky": sticky})

    def resolve_alert(self, alert_id):
        self.resolved.append(str(alert_id))

    def hide_bubble(self):
        pass


def _make_live_manager(tmp_path, monkeypatch):
    """真 manager + 真 DshMonitor（worker 不启动，手动驱动 _poll）。"""
    _qapp()
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    cfg = Config(base=tmp_path / "cfg")
    win = _InteractionWin()
    mgr = AgentLinkManager(win, cfg, min_interval=0.0)
    mon = mgr.monitors["dsh"]
    # 仿真活体监视器：收敛管线（含 _on_dsh_converged_state 的迟到信号隔离闸）
    # 只放行「监视器在跑」的信号——手动驱动 _poll/emit 的测试必须把 _running
    # 置真，否则等于在测一条生产上不存在的路径（worker 不在跑却还在发信号）。
    mon._running = True
    bridge_dir = mon.events_dir
    bridge_dir.mkdir(parents=True, exist_ok=True)
    mon._tailer._initial_backfill_done = True
    return mgr, mon, bridge_dir, win


def test_bare_approval_asked_never_raises_attention(tmp_path, monkeypatch):
    """H1：裸 approval/asked（审计信号）不锁存 waiting_approval、不弹任何气泡。

    普通工具调用（pwsh Get-Location）被宿主打上 approval/asked 是常态，
    它绝不能触发「需要看一眼」。
    """
    mgr, mon, bridge_dir, win = _make_live_manager(tmp_path, monkeypatch)
    converged = []
    mon.dsh_state_changed.connect(lambda f, t, src: converged.append(t))
    try:
        _write(bridge_dir, {"event": "approval/asked", "tool": "pwsh"})
        mon._poll()
        assert "waiting_approval" not in converged
        # 不得出现审批/attention 类弹窗（记录里的 tool 字段走的是过程汇报链，
        # 与审批语义无关——只钉「审批语义不被触发」）
        assert win.alerts == []
        assert not any("确认" in b or "看一眼" in b for b in win.bubbles)
        assert mgr._pending_interactions == {}
    finally:
        mgr.shutdown()


def test_cordis_request_run_raises_waiting_approval_once(tmp_path, monkeypatch):
    """H1：cordis/request-run（requiresApproval 严格 True）才是权威审批信号：
    锁存 waiting_approval + 专属常驻提示气泡；通用 attention 气泡不重复弹。"""
    mgr, mon, bridge_dir, win = _make_live_manager(tmp_path, monkeypatch)
    converged = []
    mon.dsh_state_changed.connect(lambda f, t, src: converged.append((t, src)))
    try:
        _write(bridge_dir, {
            "event": "cordis/request-run", "requestId": "r-1",
            "agentId": "sess-1", "sessionId": "sess-1",
            "payload": {"requiresApproval": True, "name": "构建插件", "purpose": "执行打包"},
        })
        mon._poll()
        assert ("waiting_approval", "cordis/request-run") in converged
        assert mon._converger.current_state is DshState.WAITING_APPROVAL
        # 专属常驻气泡（cordis 提示）在，通用 attention 气泡不双弹
        assert win.alerts, "cordis 审批必须有常驻提示气泡"
        assert not any("确认" in b or "看一眼" in b for b in win.bubbles),             "通用 attention 气泡不得与专属气泡双弹"
        # resolved 解锁
        _write(bridge_dir, {"event": "cordis/request-run-resolved", "requestId": "r-1",
                            "sessionId": "sess-1"})
        mon._poll()
        assert mon._converger.current_state is DshState.WORKING
    finally:
        mgr.shutdown()


def test_waiting_question_edge_does_not_double_bubble(tmp_path, monkeypatch):
    """中-1：waiting_question 边沿只更新状态；常驻问题气泡由 question 专用链呈现，
    通用 attention 气泡不得双弹。"""
    mgr, mon, bridge_dir, win = _make_live_manager(tmp_path, monkeypatch)
    converged = []
    mon.dsh_state_changed.connect(lambda f, t, src: converged.append(t))
    try:
        _write(bridge_dir, {
            "event": "question/requested", "callId": "call-1", "sessionId": "sess-1",
            "questions": [{"id": "q1", "question": "选哪个？",
                           "options": [{"label": "A"}, {"label": "B"}]}],
        })
        mon._poll()
        assert "waiting_question" in converged
        assert mon._converger.current_state is DshState.WAITING_QUESTION
        assert win.alerts, "问题必须有常驻提示气泡（question 专用链）"
        assert not any("看一眼" in b for b in win.bubbles), \
            "waiting_question 边沿不得再弹通用 attention 气泡"
    finally:
        mgr.shutdown()


def test_agent_status_waiting_approval_shows_generic_attention(tmp_path, monkeypatch):
    """显式 AgentStatus waiting_approval（无专属气泡路径）仍弹通用 attention 气泡。"""
    mgr, mon, bridge_dir, win = _make_live_manager(tmp_path, monkeypatch)
    try:
        _write(bridge_dir, {"event": "AgentStatus", "state": "waiting_approval"})
        mon._poll()
        assert mon._converger.current_state is DshState.WAITING_APPROVAL
        assert any("确认" in b or "看一眼" in b for b in win.bubbles), \
            "无专属气泡路径的审批等待必须弹通用 attention 气泡"
    finally:
        mgr.shutdown()
