# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动单元测试。

测试覆盖：
- 默认全关；
- 有界 Byte-Offset Tailer：新增行增量读取、重复读取不重放、文件轮转/截断安全、backfill 防护；
- 事件 JSONL 解析与状态规范化映射；
- AgentLinkManager 生命周期与 pause / resume；
- 状态变更触发桌宠行为与气泡反馈；
- Claude Code 确认框逻辑（拒绝则不写入 hooks）；
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import pet.agent_link as agent_link
from pet.agent_link import (
    AgentLinkManager,
    AgentEvent,
    BaseAgentMonitor,
    ByteOffsetTailer,
    DirGlobTailer,
    ClaudeCodeMonitor,
    CursorMonitor,
    CustomAgentMonitor,
    DshMonitor,
    normalize_event_state,
    opencode_event_state,
)
from pet.config import Config
from pet.config import _clean_agent_link_data, _clean_custom_agents
from pet.report_gates import REPORT_GATE_DEFAULTS
from pet.speech_bubble import SECTION_HEADER_LABEL, SECTION_HINT_LABEL


# 测试门基线：本文件验证「机制」，不隐式依赖产品默认值。
# 概率门全关（各类汇报需要用例显式开门才该弹），只保留「审批与提问」常开——它
# 是交互身份/关闭配对用例的前置条件，不属于本文件要验证的汇报抽稀行为。
# 专门校验产品默认值的用例是 TestAgentLinkManager::test_default_all_disabled，不套本基线。
_AGENT_GATE_BASELINE = {
    "state": 0.0,
    "activity": 0.0,
    "approval": 1.0,
    "done": 0.0,
    "exec_failed": 0.0,
    "model_access": 0.0,
    "stuck": 0.0,
    "bridge": 0.0,
}


def _agent_gates(**overrides) -> dict:
    """在门基线上按门名覆盖，返回**完整 8 门**字典。

    写全 8 门是刻意的：调用方普遍 `{**cfg.data["agent_link"], **patch}` 浅合并，
    部分字典会整块替换 report_gates，让未点名的门回落到产品默认（1.0 / activity 0.6），
    从而破坏基线的不确定性隔离。写全 8 门后每个用例只开自己那一类门。
    """
    gates = dict(_AGENT_GATE_BASELINE)
    gates.update(overrides)
    return gates


@pytest.fixture(autouse=True)
def _agent_gate_baseline(monkeypatch, request):
    """把 agent_link 的概率门复位为基线（产品默认值与门语义见 tests/test_report_gates.py）。"""
    if request.node.name == "test_default_all_disabled":
        yield
        return
    from pet import config as config_module

    real_defaults = config_module._default_agent_link_data
    monkeypatch.setattr(
        config_module,
        "_default_agent_link_data",
        lambda: {
            **real_defaults(),
            "report_gates": dict(_AGENT_GATE_BASELINE),
            "stuck_detect": False,
            "pattern_detect": False,
        },
    )
    yield


class TestMainlineAgentLinkHardening:
    def test_monitor_polling_uses_worker_and_stops(self, tmp_path):
        """监视器轮询不占 GUI 线程，stop 后 worker 必须退出。"""
        app = QApplication.instance() or QApplication([])
        seen = []
        ready = threading.Event()

        class ProbeMonitor(BaseAgentMonitor):
            _POLL_INTERVAL_S = 0.01

            def _poll(self, gen=None):
                seen.append(threading.get_ident())
                self._emit_state("working", self._emit_gen if gen is None else gen)
                ready.set()
                self._worker_stop.set()

        monitor = ProbeMonitor("probe", tmp_path)
        events = []
        monitor.state_event.connect(events.append)
        monitor.start()
        assert ready.wait(1.0)
        app.processEvents()
        assert seen and seen[0] != threading.get_ident()
        assert events and isinstance(events[0], AgentEvent)
        assert events[0].gen == monitor._gen
        monitor.stop()
        assert monitor._worker is not None and not monitor._worker.is_alive()

    def test_pause_buffers_events_until_resume(self, tmp_path):
        """隐藏期间落在 poll 中的状态事件在 resume 时补发。"""
        monitor = BaseAgentMonitor("probe", tmp_path)
        events = []
        monitor.state_event.connect(events.append)
        monitor.start()
        monitor.pause()
        monitor._emit_state("working", monitor._emit_gen)
        assert events == []
        monitor.resume()
        assert [event.state for event in events] == ["working"]
        monitor.stop()

    def test_stale_generation_is_rejected_at_manager(self, tmp_path):
        class Win:
            def isVisible(self):
                return True

            def mark_activity(self):
                pass

        mgr = AgentLinkManager(Win(), Config(base=tmp_path), min_interval=0.0)
        monitor = mgr.monitors["cursor"]
        monitor.start()
        old_gen = monitor._emit_gen
        monitor.stop()
        monitor.start()
        current_gen = monitor._emit_gen
        mgr._on_agent_state_event(AgentEvent("cursor", "state", gen=old_gen, state="working"))
        assert "cursor" not in mgr._last_raw
        mgr._on_agent_state_event(AgentEvent("cursor", "state", gen=current_gen, state="working"))
        assert mgr._last_raw["cursor"] == "working"
        mgr.shutdown()

    def test_install_completion_from_old_token_is_dropped(self, tmp_path):
        bubbles = []

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(Win(), cfg)
        mgr._install_pending["dsh"] = 11
        mgr._on_install_finished("dsh", True, "ok", 10)
        assert cfg.data["agent_link"]["dsh"] is False
        assert mgr._install_pending["dsh"] == 11
        assert bubbles == []
        mgr.shutdown()

    def test_state_and_activity_refresh_idle_activity_anchor(self, tmp_path):
        class Win:
            def __init__(self):
                self.activity_count = 0

            def isVisible(self):
                return True

            def mark_activity(self):
                self.activity_count += 1

        win = Win()
        mgr = AgentLinkManager(win, Config(base=tmp_path), min_interval=0.0)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_activity("dsh", "read")
        assert win.activity_count == 2
        mgr.shutdown()


# ============================================================================
# 1. ByteOffsetTailer 核心增量读取测试
# ============================================================================
class TestByteOffsetTailer:
    def test_backfill_protection_on_startup(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"event": "old1"}\n{"event": "old2"}\n', encoding="utf-8")

        tailer = ByteOffsetTailer(fpath)
        # 首次调用 read_new_lines 应当做 backfill 防护，不读取启动前的历史行
        lines = tailer.read_new_lines()
        assert lines == []
        assert tailer.offset == fpath.stat().st_size

        # 写入新行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "new1"}\n')

        new_lines = tailer.read_new_lines()
        assert len(new_lines) == 1
        assert json.loads(new_lines[0])["event"] == "new1"

    def test_no_duplicate_reads(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 初始化

        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "ev1"}\n')

        lines1 = tailer.read_new_lines()
        assert len(lines1) == 1

        # 再次调用不应重复读取
        lines2 = tailer.read_new_lines()
        assert len(lines2) == 0

    def test_file_truncation_resets_safely(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"event": "a"}\n{"event": "b"}\n', encoding="utf-8")
        tailer = ByteOffsetTailer(fpath)
        tailer.offset = 100  # 假设之前读取了较大 offset

        # 文件被清空重写（size < offset）
        fpath.write_text('{"event": "fresh"}\n', encoding="utf-8")
        tailer._initial_backfill_done = True

        lines = tailer.read_new_lines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "fresh"




class TestDirGlobTailer:
    def test_discovers_new_files_during_scan_throttle_and_keeps_offsets(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        first = tmp_path / "dsh.jsonl"
        first.write_text('{"event":"old"}\n', encoding="utf-8")
        assert tailer.read_new_lines() == []
        with first.open("a", encoding="utf-8") as f:
            f.write('{"event":"one"}\n')
        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["one"]
        second = tmp_path / "dsh-session-2.jsonl"
        second.write_text('{"event":"new-session"}\n', encoding="utf-8")
        # Directory change bypasses the long periodic interval; startup
        # backfill still skips content written before this file was discovered.
        assert tailer.read_new_lines() == []
        with second.open("a", encoding="utf-8") as f:
            f.write('{"event":"after-discovery"}\n')
        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["after-discovery"]

    def test_reads_multiple_sessions_and_rotation(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        one = tmp_path / "dsh-1.jsonl"
        two = tmp_path / "dsh-2.jsonl"
        one.touch(); two.touch()
        tailer._initial_backfill_done = True
        assert tailer.read_new_lines() == []
        one.write_text('{"event":"rotated"}\n', encoding="utf-8")
        two.write_text('{"event":"session-2"}\n', encoding="utf-8")
        events = [json.loads(x)["event"] for x in tailer.read_new_lines()]
        assert set(events) == {"rotated", "session-2"}

    def test_reset_forces_rescan(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        tailer.read_new_lines()
        path = tmp_path / "dsh-reset.jsonl"
        path.write_text('{"event":"before-reset"}\n', encoding="utf-8")
        tailer.reset()
        tailer._initial_backfill_done = True
        assert json.loads(tailer.read_new_lines()[0])["event"] == "before-reset"

    def test_glob_capped_prefers_recently_written(self, tmp_path):
        """max_files 封顶时必须优先跟踪**最近写入**的文件（活跃 DSH 实例）。

        回归背景：多 DSH 实例分区写入后，桥目录会累积 64+ 个历史 dsh-*.jsonl。
        旧实现按文件名字典序截断（`sorted(glob)`），当前活跃实例的 PID 大小
        随机，随时可能被挤出 64 上限——现象是桥接正常写盘、pet 却读不到任何
        事件（无联动、无弹窗）。修复：按 st_mtime_ns 降序，持续追加的活跃文件
        永远排最前。"""
        tailer = DirGlobTailer(tmp_path, scan_interval=60, max_files=4)
        # 造 5 个文件：4 个陈旧（较早 mtime），1 个"活跃"（最新 mtime）。
        names = [f"dsh-{pid}.jsonl" for pid in (11111, 22222, 33333, 44444, 55555)]
        for i, name in enumerate(names):
            p = tmp_path / name
            p.write_text(f'{{"event":"e{i}"}}\n', encoding="utf-8")
            ts = 1_000_000 + i  # 0..4 递增 → 55555 最新
            os.utime(p, (ts, ts))
        tailer._initial_backfill_done = True
        tailer.read_new_lines()
        tracked = {Path(p).name for p in tailer._tailers}
        assert "dsh-55555.jsonl" in tracked, "最新写入的文件必须被跟踪"
        assert "dsh-11111.jsonl" not in tracked, "最旧的文件应被挤出 64 上限"


class TestEventStateNormalization:
    def test_known_events_mapping(self):
        assert normalize_event_state("UserPromptSubmit") == "thinking"
        assert normalize_event_state("PreToolUse") == "working"
        assert normalize_event_state("PostToolUse") == "working"
        assert normalize_event_state("Stop") == "attention"
        assert normalize_event_state("SubagentStop") == "attention"
        assert normalize_event_state("PostToolUseFailure") == "error"
        assert normalize_event_state("SessionStart") == "idle"

    def test_explicit_valid_state_override(self):
        assert normalize_event_state("CustomUnknownEvent", explicit_state="thinking") == "thinking"
        # 未知事件 + 非法显式状态：返回空串表示「忽略」，绝不默认当成 working 过度触发
        assert normalize_event_state("CustomUnknownEvent", explicit_state="invalid") == ""
        assert normalize_event_state("CustomUnknownEvent") == ""


# ============================================================================
# 3. AgentLinkManager 管理器与生命周期测试
# ============================================================================
class TestAgentLinkManager:
    def test_default_all_disabled(self, tmp_path):
        """产品默认的 agent_link 形状：开关全关；汇报控制是**概率门**（非布尔开关）。

        门默认值 = 产品默认（state/done/exec_failed/model_access/stuck/bridge 常开
        1.0，activity 抽稀到 0.6，approval 常开），键序与 `_default_agent_link_data`
        一致：custom_agents 之后、stuck_detect 之前。用例名保持不改——文件头 autouse
        基线夹具按这个确切名字豁免本用例。
        """
        cfg = Config(base=tmp_path)
        assert cfg.data["agent_link"] == {
            "dsh": False,
            "claude": False,
            "cursor": False,
            "opencode": False,
            "custom_agents": [],
            "report_gates": {
                "state": 1.0,
                "activity": 0.6,
                "approval": 1.0,
                "done": 1.0,
                "exec_failed": 1.0,
                "model_access": 1.0,
                "stuck": 1.0,
                "bridge": 1.0,
            },
            "state_bubble_min_interval": 2.0,
            "stuck_detect": True,
            "stuck_worried_threshold": 3,
            "stuck_intervene_threshold": 5,
            "stuck_window_seconds": 90,
            "stuck_cooldown_seconds": 300,
            "stuck_reminder_text": "",
            "exploration_watchdog_enabled": True,
            "exploration_watchdog_warning_threshold": 3,
            "exploration_watchdog_control_threshold": 5,
            "exploration_watchdog_cooldown_steps": 3,
            "exploration_watchdog_early_grace_minutes": 5,
            "exploration_watchdog_long_run_minutes": 10,
            "exploration_watchdog_long_think_seconds": 120,
            "pattern_detect": True,
            "pattern_w6_control": 3,
            "pattern_w10_warn": 3,
            "pattern_w10_control": 4,
            "pattern_macro_w6_explore": 5,
            "pattern_macro_w6_action": 0,
            "pattern_macro_w10_explore": 7,
            "pattern_macro_w10_action": 1,
            "pattern_min_steps_between": 3,
            "pattern_cooldown_seconds": 60,
            "sound_enabled": False,
            "sound_start_path": "builtin:agent-start",
            "sound_done_path": "builtin:agent-done",
            "sound_error_path": "builtin:agent-error",
            "sound_volume": 0.65,
            "sound_cooldown_seconds": 2.0,
            "sound_start_enabled": True,
            "sound_done_enabled": True,
            "sound_error_enabled": True,
        }

        mgr = AgentLinkManager(None, cfg)
        for key, mon in mgr.monitors.items():
            assert mon.is_running() is False

    def test_enable_disable_and_pause_resume(self, tmp_path):
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)

        mgr.set_enabled("cursor", True)
        assert cfg.data["agent_link"]["cursor"] is True
        assert mgr.monitors["cursor"].is_running() is True

        # 暂停（桌宠隐藏）
        mgr.pause()
        assert mgr.monitors["cursor"].is_running() is False

        # 恢复
        mgr.resume()
        assert mgr.monitors["cursor"].is_running() is True

        # 关闭
        mgr.set_enabled("cursor", False)
        assert mgr.monitors["cursor"].is_running() is False

    def test_claude_hooks_permission_prompt(self, tmp_path, monkeypatch):
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)

        # 模拟用户拒绝
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
        ok = mgr.set_enabled("claude", True)
        assert ok is False
        assert cfg.data["agent_link"]["claude"] is False

        # 模拟用户同意
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(ClaudeCodeMonitor, "install_hooks", lambda f: True)
        ok2 = mgr.set_enabled("claude", True)
        assert ok2 is True
        assert cfg.data["agent_link"]["claude"] is True


# ============================================================================
# ============================================================================
class TestRealFileTailEndToEnd:
    def test_cursor_multi_file_tail(self, tmp_path):
        app = QApplication.instance() or QApplication([])

        # 模拟 Cursor transcripts 目录
        cursor_dir = tmp_path / ".cursor" / "projects" / "proj1" / "agent-transcripts"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = cursor_dir / "session1.jsonl"
        transcript_file.touch()

        cfg_dir = tmp_path / "dsh-config"
        cfg_dir.mkdir(parents=True, exist_ok=True)

        received_states = []
        mon = CursorMonitor(cfg_dir, base_dir=tmp_path / ".cursor" / "projects")
        mon.state_changed.connect(lambda k, s: received_states.append((k, s)))

        mon.start()
        mon._poll()  # 初始化 tailer

        # 模拟 Cursor 追加写入事件行
        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PreToolUse"}) + "\n")
            f.write(json.dumps({"type": "Stop"}) + "\n")

        mon._poll()

        assert len(received_states) == 2
        assert received_states[0] == ("cursor", "working")
        assert received_states[1] == ("cursor", "attention")

        mon.stop()

    def test_agent_state_triggers_pet_action(self, tmp_path):
        app = QApplication.instance() or QApplication([])

        switched_anims = []
        bubbles = []

        class DummyPetWindow:
            def __init__(self):
                self.cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
                self.idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched_anims.append(name)

            def request_link_anim(self, name):
                switched_anims.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched_anims.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        # 本用例验的是「状态 → 桌宠动作 + 完成提醒」的映射，所以显式开 done 门
        # （基线其它门全关；见文件头 _AGENT_GATE_BASELINE），并关掉状态气泡
        # 时间门（=0）——动画/气泡限频由 test_throttled_within_interval 单独覆盖，
        # 这里聚焦映射本身不受限频干扰。
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(done=1.0)
        ag["state_bubble_min_interval"] = 0.0
        cfg.set("agent_link", ag)
        win = DummyPetWindow()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)  # 测试关闭节流，逐个验证状态映射

        # 模拟 Agent 状态分发（busy 动作池轮换：写代码→吃Token）
        mgr._on_agent_state("claude", "thinking")
        assert "写代码" in switched_anims

        mgr._on_agent_state("claude", "working")
        assert "吃Token" in switched_anims

        mgr._on_agent_state("claude", "attention")
        # busy 后的 attention（Claude Stop=回合结束）不再立即弹「确认」气泡，
        # 改由完成确认流程接管（防双气泡）；确认后弹中性完成文案
        assert not any("确认一下" in b for b in bubbles)
        assert "claude" in mgr._done_pending
        mgr._fire_done("claude")
        assert any("已停止" in b for b in bubbles)

        # 非 busy 后独立出现的 attention 仍立即提醒
        mgr._on_agent_state("dsh", "attention")
        assert any("需要你确认" in b for b in bubbles)


# ============================================================================
# 5. 终审修复回归：hooks 格式 / 半行缓冲 / 去抖节流 / 菜单回弹
# ============================================================================
class TestClaudeHooksFormat:
    def test_hooks_written_as_matcher_arrays(self, tmp_path, monkeypatch):
        """hooks 必须是数组对象格式（matcher + hooks[{type,command}]），不是字符串。"""
        settings = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        assert ClaudeCodeMonitor.install_hooks(events_file) is True

        data = json.loads(settings.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        assert isinstance(hooks, dict)
        for name in ClaudeCodeMonitor.HOOK_EVENTS:
            entries = hooks[name]
            assert isinstance(entries, list), f"{name} 必须是数组"
            group = entries[-1]
            assert isinstance(group, dict) and "hooks" in group
            cmd = group["hooks"][0]
            assert cmd["type"] == "command"
            assert "claude_event_hook" in cmd["command"]
            assert name in cmd["command"]
            # 打包版兼容：命令不得依赖 sys.executable -c 内联执行
            assert ' -c "' not in cmd["command"]
        # 脚本文件已落地
        assert list(events_file.parent.glob("claude_event_hook.*"))

    def test_install_is_idempotent_and_preserves_user_hooks(self, tmp_path, monkeypatch):
        """重复安装不产生重复条目；用户自己的 hooks 原样保留（包括命令碰巧含 claude_event_hook 的情况）。"""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]},
                {"matcher": "Special", "hooks": [{"type": "command", "command": "custom_claude_event_hook_run"}]},
            ]},
            "other_key": 1,
        }), encoding="utf-8")
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        ClaudeCodeMonitor.install_hooks(events_file)  # 重复安装

        data = json.loads(settings.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        ours = [g for g in entries if g.get("x-dsh-pet") is True]
        theirs1 = [g for g in entries if "my-own-hook" in json.dumps(g)]
        theirs2 = [g for g in entries if "custom_claude_event_hook_run" in json.dumps(g)]
        assert len(ours) == 1  # 幂等
        assert len(theirs1) == 1  # 用户的保留
        assert len(theirs2) == 1  # 名字撞车的用户条目也保留
        assert data["other_key"] == 1

    def test_uninstall_removes_only_ours(self, tmp_path, monkeypatch):
        settings = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "user-cmd"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "user_claude_event_hook_cmd"}]},
            ]},
        }), encoding="utf-8")

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        assert ClaudeCodeMonitor.uninstall_hooks() is True

        data = json.loads(settings.read_text(encoding="utf-8"))
        stop_entries = data["hooks"]["Stop"]
        assert all(g.get("x-dsh-pet") is not True for g in stop_entries)
        assert any("user-cmd" in json.dumps(g) for g in stop_entries)
        assert any("user_claude_event_hook_cmd" in json.dumps(g) for g in stop_entries)
        # 我们独占的事件键整个移除
        assert "PreToolUse" not in data["hooks"]


class TestByteOffsetTailerPartialLine:
    def test_partial_line_buffered_not_dropped(self, tmp_path):
        """半行（无换行结尾）必须缓冲等待拼接，绝不能当整行解析或丢弃。"""
        fpath = tmp_path / "t.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 初始化

        # 写入半行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "PreTool')
        assert tailer.read_new_lines() == []  # 半行不产出

        # 补全该行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('Use"}\n{"event": "Stop"}\n')
        lines = tailer.read_new_lines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "PreToolUse"
        assert json.loads(lines[1])["event"] == "Stop"

    def test_chunk_boundary_mid_line(self, tmp_path):
        """读取窗口恰好切在行中间时，半行拼接依然正确。"""
        fpath = tmp_path / "t.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath, max_chunk_bytes=16)
        tailer.read_new_lines()

        line1 = '{"event": "PreToolUse"}\n'  # 24 bytes，跨越 16B 边界
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(line1)
        out = []
        for _ in range(3):
            out.extend(tailer.read_new_lines())
        assert out == [line1.strip()]


class TestAgentStateDebounce:
    def _make_mgr(self, tmp_path):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        switched = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                pass

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, switched, clock

    def test_same_state_deduped(self, tmp_path):
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        mgr._on_agent_state("claude", "working")
        mgr._on_agent_state("claude", "working")
        assert switched == ["写代码"]  # 只切一次

    def test_throttled_within_interval(self, tmp_path):
        """状态气泡**时间门**（state_bubble_min_interval）对动画与气泡同频限频。

        回归背景：移除 busy↔busy 节流后，working↔thinking 每 2-5s 切换让动作池
        轮换名每次不同 → window 侧立即 _switch → 反复启停 ffmpeg 解码进程
        （pet 性能下降，实测多个 reader 同时退出）。修复：动画与气泡共用一次
        时间门（_state_bubble_gate），门内都不出现，到期都恢复（保轮换语义）。"""
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        n1 = len(switched)
        assert n1 >= 1  # working 触发动画
        clock[0] += 1.0  # 1s < 2s 时间门
        mgr._on_agent_state("claude", "thinking")
        assert len(switched) == n1, "时间门内 thinking 不得重复请求动画（性能回归）"
        # 交替（working→thinking 换态）但仍在门内：也不切
        clock[0] += 0.5  # 累计 1.5s < 2s
        mgr._on_agent_state("claude", "working")
        assert len(switched) == n1, "门内交替状态也不得重复请求动画"
        # 超出门：动画按轮换序列恢复
        clock[0] += 2.1  # 累计 3.6s > 2s
        mgr._on_agent_state("claude", "thinking")
        assert len(switched) > n1, "时间门到期后动画恢复轮换"


class TestAgentMenuRebound:
    def test_decline_rolls_back_checkbox(self, tmp_path, monkeypatch):
        """用户拒绝授权后，菜单勾选态必须回滚，不允许 UI 骗人。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)
        try:
            class FakeAction:
                def __init__(self):
                    self.checked = True  # 用户刚勾上
                    self._blocked = []

                def blockSignals(self, b):
                    self._blocked.append(b)

                def setChecked(self, v):
                    self.checked = v

            act = FakeAction()
            win._toggle_agent_link("claude", True, act)
            assert act.checked is False  # 回滚
            assert cfg.data["agent_link"]["claude"] is False  # 配置未开启
        finally:
            # 窗口必须关闭：否则泄漏的真实窗口会在共享事件循环上继续推进动画链，
            # 后续测试 processEvents 时持续拉起 reader 线程（跨测试干扰）。
            win.close()
            win.deleteLater()
            # 处理 deleteLater 投递的 Qt 清理事件，避免窗口的动画/reader 事件泄漏到后续测试。
            app.processEvents()

    def test_bom_prefixed_file_tolerated(self, tmp_path):
        """PowerShell Add-Content -Encoding UTF8 会在新建文件首行写 BOM，
        tailer 必须容忍，否则 Claude hooks 产生的第一条事件永远解析失败。"""
        fpath = tmp_path / "bom.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 完成初始化（文件须先存在）
        # 外部以带 BOM 的方式重写文件（模拟轮转后首行带 BOM）
        fpath.write_bytes(b"\xef\xbb\xbf" + '{"event": "Stop"}\n'.encode("utf-8"))
        tailer.offset = 0  # 模拟轮转重置
        lines = tailer.read_new_lines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "Stop"


# ============================================================================
# ============================================================================
class TestRealFormatMappers:
    def test_cursor_role_based(self):
        from pet.agent_link import cursor_line_state
        assert cursor_line_state({"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}) == "thinking"
        assert cursor_line_state({"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell"}]}}) == "working"
        assert cursor_line_state({"role": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) == "idle"
        assert cursor_line_state({"random": True}) == ""


    def test_opencode_event_types(self):
        from pet.agent_link import opencode_event_state
        import json as j
        assert opencode_event_state("message.updated.1", j.dumps({"info": {"role": "user"}})) == "thinking"
        assert opencode_event_state("message.updated.1", j.dumps({"info": {"role": "assistant"}})) == ""
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-start"}})) == "working"
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-finish"}})) == "idle"
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-finish", "reason": "tool-calls"}})) == ""
        assert opencode_event_state("session.updated.1", "{}") == ""
        assert opencode_event_state("message.part.updated.1", "not json") == ""


class TestOpenCodeSqliteTail:
    def test_sqlite_incremental_poll(self, tmp_path):
        """OpenCode 监视器：自建 sqlite event 表，验证 backfill 防护 + 增量轮询。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])

        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("INSERT INTO event VALUES ('s1', 1, 'session.created.1', '{}')")
        db.commit()
        db.close()

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received = []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        mon._poll()  # 首次 = backfill，不产生事件
        assert received == []

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.updated.1', '{\"info\":{\"role\":\"user\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 3, 'message.part.updated.1', '{\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 4, 'session.updated.1', '{}')")
        db.commit()
        db.close()

        mon._poll()
        assert received == ["thinking", "working"]  # session.updated 被忽略
        mon.stop()

    def test_database_replacement_restarts_backfill(self, tmp_path):
        """OpenCode 重建数据库后不沿用旧 rowid，也不重放新库历史行。"""
        import os
        import sqlite3
        from pet.agent_link import OpenCodeMonitor

        db_path = tmp_path / "opencode.db"

        def make_db(path, rows):
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
            for seq, event_type, data in rows:
                db.execute("INSERT INTO event VALUES ('s1', ?, ?, ?)", (seq, event_type, data))
            db.commit()
            db.close()

        make_db(db_path, [(1, "session.created.1", "{}")])
        mon = OpenCodeMonitor(tmp_path / "cfg", db_path=db_path)
        received = []
        mon.state_changed.connect(lambda _agent, state: received.append(state))
        mon._poll()

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.updated.1', '{\"info\":{\"role\":\"user\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["thinking"]

        replacement = tmp_path / "opencode-new.db"
        make_db(replacement, [(1, "message.updated.1", '{"info":{"role":"user"}}')])
        os.replace(replacement, db_path)
        mon._poll()  # 新库既有内容只用于 backfill，不能重放
        assert received == ["thinking"]

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.part.updated.1', '{\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["thinking", "working"]


class TestCooldownUnits:
    def test_seconds_and_minutes_conversion(self, tmp_path):
        """冷却间隔秒/分钟双单位：45 秒应存为 0.75 分钟。"""
        from PySide6.QtWidgets import QApplication
        from pet.modern_settings_dialog import ModernSettingsDialog

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        dlg = ModernSettingsDialog(cfg)
        try:
            if not hasattr(dlg, "pro_cooldown_unit"):
                import pytest
                pytest.skip("非 Windows 无主动识屏设置组")

            # 切到秒，设 45 秒
            dlg.pro_cooldown_unit.setCurrentIndex(1)
            dlg.pro_cooldown_spin.setValue(45)
            assert abs(dlg._pro_cooldown_minutes() - 0.75) < 1e-9

            # 切回分钟应自动换算显示
            dlg.pro_cooldown_unit.setCurrentIndex(0)
            assert abs(dlg.pro_cooldown_spin.value() - 0.75) < 1e-9

            # 保存后配置为分钟值
            dlg._save()
            assert abs(cfg.data["proactive_screen"]["cooldown_minutes"] - 0.75) < 1e-9
        finally:
            dlg.close()
            dlg.deleteLater()


class TestMultiInstanceGlobalState:
    def test_disable_skips_uninstall_when_other_instance_enabled(self, tmp_path, monkeypatch):
        """其他实例仍开启某 Agent 联动时，本实例关闭不得卸载全局 hooks/插件。"""
        from pet.agent_link import AgentLinkManager, ClaudeCodeMonitor

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)
        mgr.set_enabled("claude", False) if False else None  # noqa

        # 直接置配置为开启（模拟另一个实例正在用）
        cfg.set("agent_link", {"claude": True})
        cfg.dir.mkdir(parents=True, exist_ok=True)
        (cfg.dir / "config-pet2.json").write_text(
            json.dumps({"agent_link": {"claude": True}}), encoding="utf-8"
        )

        calls = []
        monkeypatch.setattr(ClaudeCodeMonitor, "uninstall_hooks", classmethod(lambda cls: calls.append(1) or True))

        ok = mgr.set_enabled("claude", False)
        assert ok is True
        assert calls == []  # 另一个实例还在用 → 不卸载


class TestModernSettingsProactivePage:
    def test_proactive_page_save_roundtrip(self, tmp_path, monkeypatch):
        """现代设置面板「主动识屏」页：控件→保存→配置 回路（生产实际使用的设置页）。"""
        import sys
        if sys.platform != "win32":
            import pytest
            pytest.skip("主动识屏页仅 Windows")
        from PySide6.QtWidgets import QApplication
        from pet.modern_settings_dialog import ModernSettingsDialog
        import pet.modern_settings_dialog as settings_mod

        app = QApplication.instance() or QApplication([])
        monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
        monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda v: None)

        cfg = Config(base=tmp_path)
        dlg = ModernSettingsDialog(cfg, include_ai=True)
        dlg2 = None
        try:
            assert hasattr(dlg, "pro_enabled_check"), "主动识屏控件未构建"

            # 设置一组值并保存
            dlg.pro_enabled_check.setChecked(True)
            dlg.pro_whitelist_edit.setPlainText("code.exe\ntitle:*会议*")
            dlg.pro_cap_spin.setValue(42)
            dlg._save()

            pro = cfg.data["proactive_screen"]
            assert pro["enabled"] is True
            assert pro["whitelist"] == ["code.exe", "title:*会议*"]
            assert pro["daily_cap"] == 42
            # 未暴露字段保留
            assert "change_threshold" in pro

            # 再开一次：读回的值应与保存一致
            dlg2 = ModernSettingsDialog(cfg, include_ai=True)
            assert dlg2.pro_enabled_check.isChecked() is True
            assert dlg2.pro_cap_spin.value() == 42
        finally:
            dlg.close()
            dlg.deleteLater()
            if dlg2 is not None:
                dlg2.close()
                dlg2.deleteLater()


# ============================================================================
# 12. DSH profile 枚举（桥接插件安装/卸载目标）
# ============================================================================
class TestDshProfileEnumeration:
    """_real_profiles 只认含 package.json 的目录，过滤 node_modules 等杂项残留。"""

    def test_real_profiles_filters_node_modules_and_empty_dirs(self, tmp_path, monkeypatch):
        # issue #23：~/.dsh/profiles 下可能有 pnpm 产生的 node_modules 等杂项目录，
        # 安装/卸载桥接插件时只能枚举真实 profile（含 package.json 的目录）。
        dsh_home = tmp_path / "dsh-home"
        profiles = dsh_home / "profiles"
        for name in ("web", "headless"):
            profile = profiles / name
            profile.mkdir(parents=True)
            (profile / "package.json").write_text("{}", encoding="utf-8")
        (profiles / "node_modules").mkdir()
        (profiles / "empty-dir").mkdir()
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", dsh_home)
        assert [p.name for p in agent_link._real_profiles()] == ["headless", "web"]


# ============================================================================
# 13. Agent 联动气泡测试（开始干活 / 完成通知 / 冷却 / 抖动 / 占用延后）
# ============================================================================
class TestAgentLinkBubbles:
    def _make_mgr(self, tmp_path, agent_link_cfg=None, gates=None):
        app = QApplication.instance() or QApplication([])

        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_idle(self):
                # 与真实 window 行为对齐：清待播并回待机
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        win = DummyWin()
        win.switched = switched  # 供断言动画切换
        cfg = Config(base=tmp_path)
        if agent_link_cfg is not None:
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}
            cfg.save()
        if gates is not None:
            # 本类覆盖的是气泡机制（去抖 / 冷却 / 抖动 / 占用），不是门抽稀：
            # 显式把 state / done 开到 1.0，其余门留在基线 0.0（不隐式继承产品默认）。
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), "report_gates": _agent_gates(**gates)}
            cfg.save()

        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_working_to_idle_done_bubble(self, tmp_path):
        """1. working→idle 后，mgr._done_pending 里出现 'dsh' 的定时器；
        手动调 mgr._fire_done('dsh') 后 fake win 的 show_bubble 收到含「干完活啦」的文本。
        done 门开 1.0（原有断言依赖产品 done 默认常开），state 门保持基线关闭以免混入开始气泡。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("dsh", "working")
        assert "dsh" not in mgr._done_pending

        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        mgr._fire_done("dsh")
        assert any("已完成本轮任务" in b for b in bubbles)

    def test_done_gate_closed_no_bubble(self, tmp_path):
        """2. 同样流程但 report_gates.done=0.0 → _fire_done 后无气泡。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 0.0})
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        mgr._fire_done("dsh")
        assert bubbles == []

    def test_state_gate_start_bubble(self, tmp_path):
        """3. report_gates.state=1.0 时，thinking→working 连续两个 busy 状态只弹一次「开始干活啦」
        （第二次 prev_raw 已是 busy 不弹）；基线 state=0.0 时不弹。"""
        # 基线 state=0.0
        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)
        mgr_off._on_agent_state("dsh", "thinking")
        assert bubbles_off == []
        clock_off[0] += 3.0
        mgr_off._on_agent_state("dsh", "working")
        assert bubbles_off == []

        # state 门开到 1.0（确定性放行，无需注入 rng）
        mgr_on, win_on, bubbles_on, clock_on = self._make_mgr(
            tmp_path, gates={"state": 1.0}
        )
        mgr_on._on_agent_state("dsh", "thinking")
        assert len(bubbles_on) == 1
        # legacy 内置预设 thinking 首句（此前为 DSH 专属原文案「大肥鱼正在深度思考」）
        assert "DSH 正在思考" in bubbles_on[0]

        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        # 连续 busy 状态，thinking→working 互跳不重复弹
        assert len(bubbles_on) == 1

        # idle 后再 working → 弹「开始干活」气泡（legacy 预设 start 首句）
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "idle")
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        assert len(bubbles_on) == 2
        assert "已开始执行任务" in bubbles_on[1]

    def test_thinking_text_custom_override(self, tmp_path):
        """自定义 thinking 文案：agent_link.thinking_text 非空时优先使用，支持 {name} 占位符。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path,
            agent_link_cfg={"thinking_text": "{name} 大脑飞速运转中……"},
            gates={"state": 1.0},
        )
        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == 1
        assert "DSH 大脑飞速运转中……" == bubbles[0]
        assert "深度思考" not in bubbles[0]

        # 空字符串 → 回退默认（legacy 内置预设 thinking 首句）
        mgr2, win2, bubbles2, _ = self._make_mgr(
            tmp_path / "b", agent_link_cfg={"thinking_text": ""}, gates={"state": 1.0}
        )
        mgr2._on_agent_state("dsh", "thinking")
        assert "DSH 正在思考" in bubbles2[0]

    def test_thinking_uses_agent_delta_preset(self, tmp_path):
        """thinking 文案走统一预设 agents delta：dsh 有覆盖时命中，其它 Agent 回退 global。

        ticket 02/05：dialogue_mode=custom + {global, agents} 双层。
        """
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, gates={"state": 1.0}
        )
        cfg = mgr.cfg
        cfg.data["dialogue_mode"] = "custom"
        cfg.data["dialogue_phrases"] = {
            "global": {"thinking": ["{name} 全局思考……"]},
            "agents": {
                "dsh": {"thinking": ["{name} 大肥鱼深度思考中……"]},
                "claude": {},
            },
        }
        cfg.save()

        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == 1
        assert "大肥鱼深度思考中" in bubbles[0]
        assert "全局思考" not in bubbles[0]

        # claude 无 agents 覆盖 → 回退 global
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "thinking")
        assert any("Claude Code 全局思考" in b for b in bubbles)

    def test_jitter_cancel_done_check(self, tmp_path):
        """4. working→idle→working 抖动：idle 后 pending 存在，
        再来 working 后 pending 被清空（_cancel_done_check 生效），此后 _fire_done 不弹气泡。
        done 门必须开 1.0：否则 _fire_done 会因门关闭提前返回，断言就测不到「busy 短路」。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        clock[0] += 3.0
        mgr._on_agent_state("dsh", "working")
        assert "dsh" not in mgr._done_pending

        # 此时尝试调用 _fire_done，因为当前 last_raw 是 working（busy 状态），不弹气泡
        mgr._fire_done("dsh")
        assert bubbles == []

    def test_done_cooldown(self, tmp_path):
        """5. 冷却：clock 前进不足 5 秒时第二次 _fire_done 被 _done_cooldown 抑制；
        前进超过 5 秒后正常弹（done 门开 1.0 才能走到冷却判定）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 1
        assert "已完成本轮任务" in bubbles[0]  # done.success 预设首句

        # 再次进入 busy -> idle
        clock[0] += 3.0  # 3s < 5s 冷却
        mgr._on_agent_state("dsh", "working")
        clock[0] += 1.0  # 累计 4s < 5s
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 1  # 被冷却抑制，未新增气泡

        # 前进超过 5 秒（从第一次 _fire_done 时刻 1000.0 起算，此时 1004.0 + 2.0 = 1006.0 > 1000.0 + 5.0）
        clock[0] += 2.0
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 2
        assert "执行完成" in bubbles[1]  # 第二次 done.success 轮换到第二句

    def test_error_during_busy_done_bubble_text(self, tmp_path):
        """6. busy 期间出现 error 再 idle：完成气泡文案含「自己看一眼」而不是「干完活啦」。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("dsh", "working")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "error")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")

        # error 状态本身不立即弹气泡（由完成流程接管，防双气泡）；
        # 完成气泡应走 done.attention 预设（中性收尾），不误说成功完成
        done_bubbles = [b for b in bubbles if "已停止" in b]
        assert len(done_bubbles) == 1
        assert not any("已完成本轮任务" in b or "执行完成" in b for b in bubbles)

    def test_done_suppressed_while_pending_interaction(self, tmp_path):
        """有未决审批/选择时不弹「干完活啦」：等待用户交互的回合不算完成。

        回归背景：DSH 审批/选择等待期间 agent 状态可能短暂 idle（step 间隙），
        旧实现会弹完成气泡——用户看到"交互还没结束就报完成"。修复：_fire_done
        在存在该 agent 的 pending 阻塞交互时直接不弹。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        # 登记一条未决审批（模拟 DSH 正在等用户决定）。
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-done-gate", "approvalId": "ap-done", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh")
        # busy → idle（期间未决审批仍在）→ 确认到期：不得弹完成（气泡里只有
        # 审批常驻气泡本身，绝无"干完活啦"等完成文案）。
        mgr._on_agent_state("dsh", "working")
        clock[0] += 2.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert not any("干完活啦" in b or "已完成本轮任务" in b or "执行完成" in b for b in bubbles), \
            f"有未决审批时不得弹完成气泡，实际: {bubbles}"
        # resolved 关闭审批后，回合结束时仍可报完成——但交互发生过，_saw_alert
        # 保留（本次修复：busy 不再清 _saw_alert），done 走「停下待确认」文案
        # 而非「干完活啦」——用户不得在刚交互过的回合末尾再被"成功完成"打扰。
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-done-gate"})
        assert mgr.pending_interactions_for("dsh") == {}
        clock[0] += 6.0
        mgr._on_agent_state("dsh", "idle")  # 再次 idle（resolved 后）
        mgr._fire_done("dsh")
        assert not any("干完活啦" in b or "已完成本轮任务" in b or "执行完成" in b for b in bubbles), \
            "刚交互过的回合结束不得误报成功完成"
        assert any("已停止" in b or "待确认" in b for b in bubbles), \
            f"交互后的 done 应走「停下待确认」中性收尾，实际: {bubbles}"

    def test_bubble_busy_until_occupancy(self, tmp_path, monkeypatch):
        """7. _show_link_bubble 在 win._bubble_busy_until 为未来时间时：
        important=False 直接丢弃；important=True 时不立即弹（走 QTimer.singleShot 延后重试，测试里只需断言没有立即调用 show_bubble）。

        回归锚点（F1）：hold_bubble 写 time.monotonic() 域（真实 window 行为），
        _show_link_bubble 必须用同域比较——曾误用 time.time()（epoch 秒）导致
        门禁恒失效。stub 以 monotonic 未来时刻模拟占用。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        win._bubble_busy_until = time.monotonic() + 100.0

        # important=False 丢弃
        mgr._show_link_bubble("普通消息", important=False)
        assert bubbles == []

        # important=True 走 singleShot 延后重试，不立即调用 show_bubble
        mgr._show_link_bubble("重要消息", important=True)
        assert bubbles == []

    def test_idle_to_working_same_batch_not_throttled(self, tmp_path):
        """同批到达的 idle→working（时间差 <2s）不得被节流吞掉「开始干活」气泡。

        回归背景：DSH 桥接 80ms 批量 flush，idle（心跳）与 working（开始干活）
        经常落同一批，时间差 <2s。旧节流逻辑对「任意状态切换」生效，把进入
        工作的 working 吞掉——实测启动后 start/think 气泡整轮消失（只有音效）。
        修复：节流只对 busy↔busy 互跳生效，idle/sleeping→busy 的进入工作不节流。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"state": 1.0})
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 0.1  # < min_interval(2.0)
        mgr._on_agent_state("dsh", "working")
        assert any("已开始执行任务" in b or "开始干活" in b for b in bubbles), \
            f"idle→working 同批不得被节流吞掉开始干活气泡: {bubbles}"

    def test_busy_to_busy_still_throttled(self, tmp_path):
        """busy↔busy 互跳（working↔thinking）在 2 秒内仍被节流（思考/工作文案不刷屏）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"state": 1.0})
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 0.1
        mgr._on_agent_state("dsh", "working")
        n1 = len(bubbles)
        clock[0] += 0.5  # < 2s
        mgr._on_agent_state("dsh", "thinking")
        n2 = len(bubbles)
        assert n2 == n1, "busy→busy 互跳在 2s 内不得再弹气泡"
        clock[0] += 2.1  # > 2s
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 0.1
        mgr._on_agent_state("dsh", "working")
        assert len(bubbles) > n2, "idle→working 再次进入工作应正常弹"

    def test_busy_to_attention_counts_as_done(self, tmp_path):
        """8. Claude 风格：working→attention(Stop) 进入完成确认，不弹立即提醒，
        确认后弹完成气泡（因见过 attention 用中性文案）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "attention")
        assert "claude" in mgr._done_pending  # 进入完成确认
        assert bubbles == []  # 不弹立即 attention 气泡（防双气泡）

        mgr._fire_done("claude")
        assert any("已停止" in b for b in bubbles)
        assert not any("已完成本轮任务" in b or "执行完成" in b for b in bubbles)

    def test_thinking_after_working_emits_bubble_gated_by_time(self, tmp_path):
        """working → thinking 弹思考气泡（旧节流曾把 thinking 与工作动画一起
        挡掉，用户实测状态机收敛但无思考气泡；DSH 是 working→thinking→working
        →thinking 交替，非连续 thinking）；状态气泡共用 state_bubble_min_interval
        时间门（默认 2.0s）：working 弹过后 2s 内的 thinking 被限频跳过，
        间隔足够后交替的 thinking 放行——防刷屏同时保证 thinking 可感知。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"state": 1.0})
        # working（AgentStatus）先到 → 开始干活气泡
        mgr._on_agent_state("dsh", "working")
        n0 = len(bubbles)
        assert n0 == 1
        # 0.5s 后 thinking：时间门内 → 跳过（防刷屏）
        clock[0] += 0.5
        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == n0, f"working 后 2s 内 thinking 被时间门限频: {bubbles}"
        # 回到 working（DSH 交替），再隔 2.1s 到 thinking：时间门到期 → 弹
        clock[0] += 0.5
        mgr._on_agent_state("dsh", "working")
        clock[0] += 2.1
        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) > n0, f"时间门到期后交替 thinking 应弹思考气泡: {bubbles}"
        assert any("思考" in b for b in bubbles), "思考气泡应含思考文案"

    def test_standalone_attention_immediate_bubble(self, tmp_path):
        """9. 非 busy 后独立出现的 attention：立即提醒，不进完成流程。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "attention")
        assert "claude" not in mgr._done_pending
        assert any("确认一下" in b for b in bubbles)

    def test_done_restores_idle_anim_unless_others_busy(self, tmp_path):
        """10. 完成确认后恢复待机动画（Claude 没有 idle 事件，靠这步回待机）；
        另有 Agent 在忙时不恢复（避免顶掉对方的工作动画）。done 门开 1.0：
        恢复待机发生在门判定之后，门关着就永远走不到。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "idle")
        mgr._fire_done("claude")
        assert win.switched[-1] == "待机呼吸"  # 恢复待机
        assert mgr._last_applied["claude"][0] == "idle"

        # 另一 Agent 在忙：不恢复
        mgr2, win2, bubbles2, clock2 = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr2._on_agent_state("claude", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("dsh", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("claude", "idle")
        switched_before = len(win2.switched)
        mgr2._fire_done("claude")
        assert len(win2.switched) == switched_before  # 没有切回待机

    def test_opencode_step_finish_tool_calls_no_done(self, tmp_path):
        """回归（PR57 合并时丢失的 main 侧用例）：opencode step-finish
        (reason=tool-calls)（派 task 子代理后主代理停笔等待）不触发完成确认——
        不产出 idle 状态 → 800ms 确认不排程；step-finish(reason=stop) 才是
        真结束 → 排程并出完成气泡。产品逻辑（opencode_event_state 的
        tool-calls→""）仍存在，恢复端到端守卫。"""
        import json as j

        mgr, win, bubbles, clock = self._make_mgr(tmp_path, gates={"done": 1.0})
        mgr._on_agent_state("dsh", "working")

        # 子代理长跑期间：tool-calls 维持现状，确认窗口不排程
        state = opencode_event_state("message.part.updated.1",
                                     j.dumps({"part": {"type": "step-finish", "reason": "tool-calls"}}))
        assert state == ""
        if state:  # 与 agent_link._poll 的空状态跳过逻辑一致
            mgr._on_agent_state("dsh", state)
        clock[0] += 3.0
        assert "dsh" not in mgr._done_pending
        assert bubbles == []

        # 子代理回注、整轮真结束：stop → idle → 排程 → 完成气泡恰一次
        state = opencode_event_state("message.part.updated.1",
                                     j.dumps({"part": {"type": "step-finish", "reason": "stop"}}))
        assert state == "idle"
        mgr._on_agent_state("dsh", state)
        assert "dsh" in mgr._done_pending
        mgr._fire_done("dsh")
        assert any("已完成" in b for b in bubbles), f"应弹完成气泡: {bubbles}"


class TestAgentLinkSounds:
    def _make(self, tmp_path, monkeypatch, **sound_cfg):
        class Win:
            _bubble_busy_until = 0.0
            cats = {"acts": ["写代码"]}
            idles = ["待机"]

            def isVisible(self):
                return True

            def request_link_anim(self, _name):
                pass

            def request_link_idle(self):
                pass

            def show_bubble(self, *_args, **_kwargs):
                pass

        cfg = Config(base=tmp_path)
        cfg.data["agent_link"].update({
            "sound_enabled": True,
            # 音效本身不经过概率门（_emit_sound 在 _report_allowed 之前），但本类驱动的
            # 是完整的「开始 → 完成 / 出错」生命周期：把 state/done 开到 1.0，让同一条
            # 状态流的气泡分支也照常走，避免用例只在半条链路上取证。
            "report_gates": _agent_gates(state=1.0, done=1.0),
            **sound_cfg,
        })
        sound = tmp_path / "sound.wav"
        sound.write_bytes(b"RIFF")
        monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda _path: sound)
        calls = []
        monkeypatch.setattr(agent_link, "play_sound", lambda path, volume=1.0: calls.append((path, volume)) or True)
        clock = [100.0]
        mgr = AgentLinkManager(Win(), cfg, min_interval=0.0, clock=lambda: clock[0])
        return mgr, clock, calls

    def test_start_done_error_events_play_at_confirmed_points(self, tmp_path, monkeypatch):
        mgr, clock, calls = self._make(tmp_path, monkeypatch, sound_cooldown_seconds=0.0)
        mgr._on_agent_state("dsh", "working")
        assert len(calls) == 1
        clock[0] += 1
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(calls) == 2
        clock[0] += 1
        mgr._on_agent_state("dsh", "working")
        clock[0] += 1
        mgr._on_agent_state("dsh", "error")
        assert len(calls) == 4, "新忙碌周期应有 start + error 两个事件音效"
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(calls) == 4, "error 周期不能追加 done 音效"

        clock[0] += 1
        mgr._on_agent_state("cursor", "working")
        clock[0] += 1
        mgr._on_agent_state("cursor", "error")
        clock[0] += 1
        mgr._on_agent_state("cursor", "working")
        mgr._on_agent_state("cursor", "idle")
        mgr._fire_done("cursor")
        assert len(calls) == 7, "error 后重试仍属于同一错误周期，不能补 done"

    def test_cooldown_is_global_and_disabled_is_silent(self, tmp_path, monkeypatch):
        mgr, clock, calls = self._make(tmp_path, monkeypatch, sound_cooldown_seconds=2.0)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("claude", "working")
        assert len(calls) == 1
        clock[0] += 2.01
        mgr._on_agent_state("claude", "error")
        assert len(calls) == 2

        mgr.cfg.data["agent_link"]["sound_enabled"] = False
        clock[0] += 3
        mgr._on_agent_state("cursor", "working")
        assert len(calls) == 2


class TestInstallErrorSummary:
    def test_install_bridge_auto_installs_pnpm(self, tmp_path, monkeypatch):
        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        profile = tmp_path / "profiles" / "default"
        profile.mkdir(parents=True)
        manifest = profile / "package.json"
        manifest.write_text("{}", encoding="utf-8")

        pnpm_cli = str(tmp_path / "pnpm.mjs")
        located = iter([None, pnpm_cli, pnpm_cli])
        monkeypatch.setattr(agent_link, "_find_pnpm_cli", lambda: next(located))
        monkeypatch.setattr(agent_link, "_npm_cli", lambda: "C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js")
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(
            agent_link.shutil, "which",
            lambda name: "C:/Program Files/nodejs/node.exe" if name == "node" else None,
        )

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[1] == pnpm_cli:
                manifest.write_text(
                    json.dumps({"dependencies": {agent_link.DSH_PLUGIN_NAME: "file:bridge"}}),
                    encoding="utf-8",
                )
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(agent_link.subprocess, "run", fake_run)

        ok, message, _ = DshMonitor.install_bridge()

        assert ok is True
        assert "1 个 dsh 实例" in message
        assert calls[0][0] == [
            "C:/Program Files/nodejs/node.exe",
            "C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js",
            "install", "-g", "pnpm",
        ]
        assert calls[1][0] == [
            "C:/Program Files/nodejs/node.exe", pnpm_cli, "add", str(plugin),
        ]
        assert json.loads(manifest.read_text(encoding="utf-8"))["dsh"]["profile"]["bundles"] == [
            agent_link.DSH_PLUGIN_NAME
        ]

    def test_find_pnpm_cli_accepts_homebrew_javascript_symlink(self, tmp_path, monkeypatch):
        """回归（PR57 合并时丢失的 main 侧用例）：homebrew 的 pnpm 是
        bin/pnpm → lib/node_modules/pnpm/bin/pnpm.cjs 的符号链接，
        _find_pnpm_cli 必须经 resolve() 落到真实 JS CLI。
        注：Windows 无管理员权限创建 symlink 会失败（WinError 1314），
        该用例在 Linux/macOS CI 执行；本地 Windows 跳过。"""
        try:
            cli = tmp_path / "lib" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs"
            cli.parent.mkdir(parents=True)
            cli.write_text("", encoding="utf-8")
            shim = tmp_path / "bin" / "pnpm"
            shim.parent.mkdir()
            shim.symlink_to(cli)
        except OSError:
            pytest.skip("symlink 权限不可用（Windows 无管理员）")
        monkeypatch.delenv("DSH_PNPM_BIN", raising=False)
        monkeypatch.setattr(agent_link, "_which", lambda name: str(shim) if name == "pnpm" else None)
        assert agent_link._find_pnpm_cli() == str(cli)

    def test_extract_err_pnpm_line(self):
        output = """
        [1/4] Resolving packages...
        node_modules/some-pkg/index.js
        at Object.<anonymous> (file:///C:/Users/test/AppData/Roaming/npm/node_modules/dsh/dist/index.js:2:14)
        ERR_PNPM_FETCH_404 GET https://registry.npmjs.org/not-found: Not Found - 404
        at async install (file:///C:/Users/test/AppData/Roaming/npm/node_modules/dsh/dist/install.js:10:5)
        """
        summary = DshMonitor._summarize_install_error(output)
        assert "ERR_PNPM_FETCH_404" in summary
        assert "at " not in summary
        assert "node_modules" not in summary

    def test_pure_stack_returns_unknown_error(self):
        output = """
        at Object.<anonymous> (file:///C:/Users/test/index.js:1:1)
        at Module._compile (node:internal/modules/cjs/loader:1100:14)
        node_modules/foo/bar.js
        """
        summary = DshMonitor._summarize_install_error(output)
        assert summary == "未知错误"

    def test_long_line_truncated_within_60_chars(self):
        output = (
            "Error: "
            + "A" * 100
            + " something happened at C:\\very\\long\\directory\\path\\to\\file.js"
        )
        summary = DshMonitor._summarize_install_error(output)
        assert len(summary) <= 60
        assert summary.startswith("Error:")


class TestUninstallBridgeWithoutPnpm:
    """没有 pnpm 时关闭联动不能假成功：manifest 里的 link: 残留会指向被删目录。

    2026-09 dsh 事故同型——profile 的 package.json 还挂着 link:<即将删除的程序
    目录>，dsh 启动时解析失败拖垮整个插件树。旧实现 _pnpm_command() is None
    直接 return True，什么都不改。
    """

    def _profile(self, tmp_path, deps, bundles=None):
        profile = tmp_path / "profiles" / "web"
        profile.mkdir(parents=True)
        data = {"dependencies": dict(deps)}
        if bundles is not None:
            data["dsh"] = {"profile": {"bundles": list(bundles)}}
        (profile / "package.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return profile

    def test_no_pnpm_removes_manifest_entries_with_backup(self, tmp_path, monkeypatch):
        """无 pnpm：备份 → 删依赖条目 → 清 bundles → 删链接 → 返回 True。"""
        plugin = tmp_path / "old-build" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}", "keep-me": "^1.0.0"},
            bundles=[agent_link.DSH_PLUGIN_NAME, "other-bundle"],
        )
        linked = profile / "node_modules" / "@dsh-pet" / "bridge"
        linked.mkdir(parents=True)
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True

        manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dependencies"], \
            "link: 残留必须删掉（否则指向即将删除的程序目录）"
        assert manifest["dependencies"]["keep-me"] == "^1.0.0", "无关依赖不许动"
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dsh"]["profile"]["bundles"]
        assert "other-bundle" in manifest["dsh"]["profile"]["bundles"]
        assert list(profile.glob("package.json.bak-*")), "手改前必须备份"
        assert not linked.exists(), "profile 内的插件链接应尽力清理"

    def test_no_pnpm_profile_without_plugin_is_noop(self, tmp_path, monkeypatch):
        """未安装的 profile 幂等成功，且不该产生备份。"""
        profile = self._profile(tmp_path, deps={"keep-me": "^1.0.0"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True
        assert not list(profile.glob("package.json.bak-*"))

    def test_backups_pruned_to_recent_five(self, tmp_path, monkeypatch):
        """manifest 备份只保留最近 5 份：卸载/修复都会持续产 bak，需有清理。"""
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: "link:W:/gone/bridge"},
            bundles=[agent_link.DSH_PLUGIN_NAME],
        )
        fakes = {profile / f"package.json.bak-2026090{i}-12000{i}" for i in range(7)}
        for fake in fakes:
            fake.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True

        backups = sorted(profile.glob("package.json.bak-*"))
        assert len(backups) == 5, f"备份应清理到最近 5 份，现有 {len(backups)}"
        kept = {b.name for b in backups}
        fake_names = {fake.name for fake in fakes}
        assert kept & fake_names, "应保留 7 份旧备份中最新的 4 份"
        assert len(kept - fake_names) == 1, "本次卸载新建的备份必须在其中"
        assert not any(n < "package.json.bak-20260903" for n in kept), "最旧的 3 份必须被清掉"

    def test_with_pnpm_still_uses_pnpm_remove(self, tmp_path, monkeypatch):
        """有 pnpm 时保持现状：走 pnpm remove，再清 bundles，不做 JSON 手改备份。"""
        plugin = tmp_path / "current-build" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"},
            bundles=[agent_link.DSH_PLUGIN_NAME],
        )
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls = []

        def fake_run(profile_dir, *args):
            calls.append(args)
            data = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
            data["dependencies"].pop(agent_link.DSH_PLUGIN_NAME, None)
            (profile_dir / "package.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        assert DshMonitor.uninstall_bridge() is True
        assert calls == [("remove", agent_link.DSH_PLUGIN_NAME)]
        manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dsh"]["profile"]["bundles"]
        assert not list(profile.glob("package.json.bak-*")), "pnpm 路径不做手改备份"


# ============================================================================
# 14. Agent 动作轮换、过程汇报与 window 平滑衔接测试
# ============================================================================
class TestAgentLinkChainingAndActivity:
    def _make_mgr(self, tmp_path, agent_link_cfg=None, acts=None):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "吃Token", "轻快记录", "漂浮踏步"] if acts is None else acts}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                switched.append(self.idles[0])

        win = DummyWin()
        win.switched = switched
        cfg = Config(base=tmp_path)
        if agent_link_cfg is not None:
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}
            cfg.save()

        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_anim_rotation_sequence(self, tmp_path):
        """1. 动作池轮换顺序：DummyWin 的 cats.acts 含 ['写代码','吃Token','轻快记录','漂浮踏步']，
        连续 6 次 busy（每次 clock 前进 3s 避免节流）→ 依次为 写代码/吃Token/轻快记录/写代码/吃Token/漂浮踏步（每第3次插播摸鱼）。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, acts=["写代码", "吃Token", "轻快记录", "漂浮踏步"]
        )
        res = [mgr._next_link_anim_rotation() for _ in range(6)]
        expected = ["写代码", "吃Token", "轻快记录", "吃Token", "写代码", "漂浮踏步"]
        assert res == expected

    def test_anim_rotation_falls_back_to_keywords_and_available_acts(self, tmp_path):
        """精确动作名不存在时，按主/摸鱼关键词选择；完全不匹配时回退到任意动作。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, acts=["敲击键盘", "伸懒腰", "发呆"]
        )
        res = [mgr._next_link_anim_rotation() for _ in range(6)]
        assert res == ["敲击键盘", "敲击键盘", "伸懒腰", "敲击键盘", "敲击键盘", "伸懒腰"]

        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=["跳舞"])
        assert mgr._next_link_anim_rotation() == "跳舞"


    def test_empty_acts_returns_none(self, tmp_path):
        """2. 无可用动作时 _next_link_anim_rotation 返回 None（DummyWin cats.acts 为空列表）不抛异常。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=[])
        assert mgr._next_link_anim_rotation() is None
        # 触发状态变更也不抛异常
        mgr._on_agent_state("dsh", "working")
        assert win.switched == []

    def test_activity_reporting(self, tmp_path):
        """3. 过程汇报：report_gates.activity=1.0 时 mgr._on_agent_activity('dsh','bash') → 气泡含「正在跑命令」；
        10 秒内第二次任何工具不弹；同工具 60 秒内不重复（clock 前进 15s 再发 bash 仍不弹；换成 read 则弹「正在读文件」）；
        全局限流 8s（另一 agent 在 8s 内也不弹）。activity 门关闭时不弹（本文件基线默认 0.0，
        见文件头 _AGENT_GATE_BASELINE；产品默认值是 0.6 的过程汇报抽稀）。
        未知工具（如 'frobnicate'）弹安全兜底文案。"""
        # activity 门关着（基线 0.0）时不弹
        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)
        mgr_off._on_agent_activity("dsh", "bash")
        assert bubbles_off == []

        # activity 门开到 1.0（确定性全放行，避免 0.6 抽稀导致断言不确定）
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, agent_link_cfg={"report_gates": _agent_gates(activity=1.0)}
        )

        # 未知工具弹安全兜底文案，不泄露原始参数
        mgr._on_agent_activity("dsh", "frobnicate")
        assert len(bubbles) == 1
        assert "正在调用工具" in bubbles[-1]
        assert "frobnicate" not in bubbles[-1]

        # dsh bash → 弹「正在跑命令」
        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "bash")
        assert len(bubbles) == 2
        assert "正在跑命令" in bubbles[-1]

        # 10 秒内第二次任何工具不弹
        clock[0] += 5.0
        mgr._on_agent_activity("dsh", "read")
        assert len(bubbles) == 2

        # 全局限流 8s（另一 agent 在 8s 内也不弹，从 1000.0 起算此时 1005.0 < 1008.0）
        mgr._on_agent_activity("claude", "read")
        assert len(bubbles) == 2

        # 同工具 60 秒内不重复：前进 15s（总共 +20s > 10s，但 < 60s），再发 bash 仍不弹
        clock[0] += 15.0
        mgr._on_agent_activity("dsh", "bash")
        assert len(bubbles) == 2

        # 换成 read 则弹「正在读文件」
        mgr._on_agent_activity("dsh", "read")
        assert len(bubbles) == 3
        assert "正在读文件" in bubbles[-1]

        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "pwsh")
        assert len(bubbles) == 4
        assert "pwsh" in bubbles[-1]  # activity.run 轮换到含工具名的变体
        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "memory_search")
        assert len(bubbles) == 5
        assert bubbles[-1].strip()  # activity.default 轮换文案，仅断言有气泡

    def test_activity_bubble_receives_tool_record_fields(self, tmp_path):
        """过程汇报气泡必须拿到上游 tool/call 记录的字段（显式注入，非隐式上下文）。

        监视器同轮转发的工具记录被按 agent 缓存，_on_agent_activity 把
        tool/label/command/argsKey/callId/step + 会话字段显式传给模板；
        条件字段缺失时占位符自动隐藏（不原样露出 {target} 等死占位符）。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, agent_link_cfg={"report_gates": _agent_gates(activity=1.0)}
        )
        # 模拟监视器 _poll 的同轮顺序：先 raw_record（工具记录），再 activity 信号
        # 字段以桥接真实写出的 tool/call 为准（tool/argsKey/command/callId/step）。
        mgr._remember_dialogue_record("dsh", {
            "ts": 1, "event": "tool/call", "tool": "read", "command": "cat src/app.py",
            "argsKey": "a1b2", "callId": "call-1", "step": 2,
            "sessionId": "sess-1",
        })
        cfg = mgr.cfg
        cfg.data["dialogue_mode"] = "custom"
        cfg.data["dialogue_phrases"] = {
            "activity.read": ["正在读取（{tool}，第 {step} 步，命令 {command}）"],
            "activity.search": ["搜索（{tool}）argsKey={argsKey}"],
        }
        cfg.save()
        mgr._on_agent_activity("dsh", "read")
        assert bubbles, "气泡未弹出"
        assert "第 2 步" in bubbles[-1]
        assert "cat src/app.py" in bubbles[-1]

        # 字段缺失的最小记录：条件占位符自动隐藏，不原样保留 {command}/{step}
        clock[0] += 15.0
        mgr._remember_dialogue_record("dsh", {"ts": 2, "event": "tool/call", "tool": "grep"})
        mgr._on_agent_activity("dsh", "grep")
        assert "argsKey=a1b2" not in bubbles[-1]
        assert "{command}" not in bubbles[-1]
        assert "{step}" not in bubbles[-1]
        assert "{argsKey}" not in bubbles[-1]

    def test_window_smooth_chaining(self, tmp_path):
        """4. window 侧平滑衔接（用真实 PetWindow + MovieLibrary，offscreen，参考 TestAgentMenuRebound 的构造）：
        win._switch('优雅女仆舞')（一次性动作）后 win.request_link_anim('写代码') →
        当前 anim 仍是 '优雅女仆舞' 且 _pending_link_anim=='写代码'（不打断）；
        手动调 win._on_anim_ended('优雅女仆舞') → anim 变为 '写代码'。
        再测：待机中（win.anim 在 win.idles 里）request_link_anim 立即切换。
        request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        try:
            # 确保 '优雅女仆舞' 是一次性动作 (acts)
            assert "优雅女仆舞" in win.acts
            win._switch("优雅女仆舞")
            assert win.anim == "优雅女仆舞"
            assert win._is_one_shot_playing() is True

            win.request_link_anim("写代码")
            assert win.anim == "优雅女仆舞"
            assert win._pending_link_anim == "写代码"

            # 手动调 _on_anim_ended('优雅女仆舞') → 播放待播的 '写代码'
            win._on_anim_ended("优雅女仆舞")
            assert win.anim == "写代码"
            assert win._pending_link_anim is None

            # 待机中（win.anim 在 win.idles 里）request_link_anim 立即切换
            idle_name = win.idles[0]
            win._switch(idle_name)
            assert win.anim in win.idles
            assert win._is_one_shot_playing() is False

            win.request_link_anim("吃Token")
            assert win.anim == "吃Token"

            # request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）
            win._switch("优雅女仆舞")
            win._pending_link_anim = "写代码"
            win.request_link_idle()
            assert win.anim == "优雅女仆舞"
            assert win._pending_link_anim is None
        finally:
            win.close()
            win.deleteLater()

    def test_on_anim_ended_continuation(self, tmp_path):
        """5. _on_anim_ended 联动续播：构造 PetWindow 后，设置 win._link_anim_current='写代码'，
        win._link_next_provider=lambda: '吃Token'，调 win._on_anim_ended('写代码') → anim=='吃Token'；
        provider 返回 None 时走正常动画链（不抛异常即可）。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        try:
            win._link_anim_current = "写代码"
            win._link_next_provider = lambda: "吃Token"
            win._on_anim_ended("写代码")
            assert win.anim == "吃Token"
            assert win._link_anim_current == "吃Token"

            # provider 返回 None 时走正常动画链（不抛异常）
            win._link_next_provider = lambda: None
            win._on_anim_ended("吃Token")
            # 正常推进，不抛异常
            assert win._link_anim_current is None
        finally:
            win.close()
            win.deleteLater()



# ============================================================================
# 14. 过程汇报：事件 tool 字段 → activity 信号
# ============================================================================
class TestActivitySignal:
    def test_tool_field_emits_activity_without_state(self, tmp_path):
        """jsonl 事件带 tool 字段时发 activity 信号，且不产生状态变化。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got, states = [], []
        mon.activity.connect(lambda a, t: got.append((a, t)))
        mon.state_changed.connect(lambda a, s: states.append(s))
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()  # 先建空文件，backfill 才能落到末尾
        mon._tailer.read_new_lines()  # backfill 初始化
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"tool/call","tool":"bash"}\n')
        mon._poll()
        assert got == [("dsh", "bash")]
        assert states == []

    def test_no_tool_no_activity(self, tmp_path):
        """普通状态事件不发 activity。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got = []
        mon.activity.connect(lambda a, t: got.append(t))
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._tailer.read_new_lines()
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"AgentStatus","state":"working"}\n')
        mon._poll()
        assert got == []

    class _HiddenWin:
        cats = {"acts": ["写代码"]}
        idles = ["待机呼吸"]
        _bubble_busy_until = 0.0
        switched = None
        bubbles = None

        def __init__(self):
            self.switched = []
            self.bubbles = []

        def isVisible(self):
            return False

        def _switch(self, name):
            self.switched.append(name)

        def show_bubble(self, text, duration_ms=3000):
            self.bubbles.append(text)

        def _pick(self, lst):
            return lst[0]

    def test_fire_done_hidden_window_is_noop(self, tmp_path):
        """opus 评审 H1：隐藏窗口上 _fire_done 不得切动画/弹气泡。"""
        app = QApplication.instance() or QApplication([])
        win = TestActivitySignal._HiddenWin()
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(win, cfg)
        mgr._last_raw["dsh"] = "idle"
        mgr._fire_done("dsh")
        assert win.switched == []
        assert win.bubbles == []

    def test_pause_cancels_done_pending(self, tmp_path):
        """opus 评审 H1：pause 必须取消所有完成确认计时器。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        bubbles = []

        class Win:
            cats = {"acts": ["写代码"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                pass

            def request_link_idle(self):
                pass

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        mgr = AgentLinkManager(Win(), cfg)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending
        mgr.pause()
        assert mgr._done_pending == {}

# ============================================================================
# 15. OpenCode 子代理会话过滤（防「干完活啦」刷屏）
# ============================================================================
class TestOpenCodeSubagentFilter:
    def _make_db(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
        db.execute("INSERT INTO session VALUES ('root1', NULL)")
        db.execute("INSERT INTO session VALUES ('child1', 'root1')")
        db.commit()
        db.close()
        return db_path

    def test_subagent_events_filtered(self, tmp_path):
        """子代理（parent_id 非空）会话的 step-start/step-finish/工具事件全部忽略。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])
        db_path = self._make_db(tmp_path)
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received, tools = [], []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.activity.connect(lambda k, t: tools.append(t))
        mon.start()
        mon._poll()  # backfill

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('c', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 2, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"tool\",\"tool\":\"bash\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 3, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-finish\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == [] and tools == []  # 子代理全程静默

        # 主会话正常报
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('r', 4, 'message.part.updated.1', "
                   "'{\"sessionID\":\"root1\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["working"]
        mon.stop()

    def test_missing_session_table_conservative(self, tmp_path):
        """老库没有 session 表：不过滤（保守不丢事件）。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.commit()
        db.close()
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received = []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        mon._poll()
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"x\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["working"]
        mon.stop()

    def test_busy_agent_owns_process(self, tmp_path):
        """联动去重：联动开启+忙碌+进程匹配 → True；其余组合 → False。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        cfg.data["agent_link"]["opencode"] = True

        class W:
            cats = {"acts": []}
            idles = []
            _bubble_busy_until = 0.0
            def isVisible(self): return True
            def show_bubble(self, *a, **k): pass

        mgr = AgentLinkManager(W(), cfg)
        mgr._last_raw["opencode"] = "working"
        assert mgr.busy_agent_owns_process("OpenCode.exe") is True
        assert mgr.busy_agent_owns_process("msedge.exe") is False
        mgr._last_raw["opencode"] = "idle"
        assert mgr.busy_agent_owns_process("OpenCode.exe") is False
        mgr._last_raw["opencode"] = "working"
        cfg.data["agent_link"]["opencode"] = False  # 联动关闭时不抑制识屏
        assert mgr.busy_agent_owns_process("OpenCode.exe") is False
        # dsh 无独立进程：靠窗口标题识别
        cfg.data["agent_link"]["dsh"] = True
        mgr._last_raw["dsh"] = "working"
        assert mgr.busy_agent_owns_process("msedge.exe", "审查结果 — DeepSeek Harness") is True
        assert mgr.busy_agent_owns_process("msedge.exe", "哔哩哔哩") is False
        mgr._last_raw["dsh"] = "idle"
        assert mgr.busy_agent_owns_process("msedge.exe", "审查结果 — DeepSeek Harness") is False


# ============================================================================
# 自定义联动 Agent（agent_link.custom_agents 配置驱动）
# ============================================================================
class TestCustomAgentConfigCleaning:
    def test_valid_entry_kept_and_normalized(self):
        cleaned = _clean_custom_agents([
            {"key": "Gemini", "name": "  Gemini CLI  ", "path": " ~/.gemini/ev.jsonl "},
        ])
        assert cleaned == [{"key": "gemini", "name": "Gemini CLI", "path": "~/.gemini/ev.jsonl"}]

    def test_name_defaults_to_key(self):
        cleaned = _clean_custom_agents([{"key": "myagent", "path": "~/x.jsonl"}])
        assert cleaned == [{"key": "myagent", "name": "myagent", "path": "~/x.jsonl"}]

    def test_invalid_entries_dropped(self):
        cleaned = _clean_custom_agents([
            "not-a-dict",                                # 非对象
            {"key": "Bad Key", "path": "~/x.jsonl"},     # key 含空格/大写
            {"key": "claude", "path": "~/x.jsonl"},      # 与内置键冲突
            {"key": "ok", "path": ""},                   # 空 path
            {"key": "ok2"},                              # 缺 path
        ])
        assert cleaned == []

    def test_duplicate_keys_deduped(self):
        cleaned = _clean_custom_agents([
            {"key": "gemini", "path": "~/a.jsonl"},
            {"key": "gemini", "path": "~/b.jsonl"},
        ])
        assert len(cleaned) == 1
        assert cleaned[0]["path"] == "~/a.jsonl"

    def test_max_entries_truncated(self):
        raw = [{"key": f"agent{i}", "path": f"~/{i}.jsonl"} for i in range(20)]
        assert len(_clean_custom_agents(raw)) == 8

    def test_non_list_returns_empty(self):
        assert _clean_custom_agents(None) == []
        assert _clean_custom_agents({"key": "gemini"}) == []

    def test_clean_agent_link_data_cleans_and_keeps_custom_key_booleans(self):
        cleaned = _clean_agent_link_data({
            "custom_agents": [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}],
            "gemini": True,        # 自定义键的开关布尔（set_enabled 写入路径）
            "notify_done": False,  # 旧布尔开关：一次性迁移进 done 门，且不再写回旧键
        })
        assert cleaned["custom_agents"] == [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}]
        assert cleaned["gemini"] is True
        # 旧开关被弹出（配置形状里不留兼容别名），语义落到概率门上：False → done=0.0；
        # 其余门取产品默认（activity 抽稀到 0.6），不受旧键迁移影响。
        assert "notify_done" not in cleaned
        assert cleaned["report_gates"] == {**REPORT_GATE_DEFAULTS, "done": 0.0}


class TestCustomAgentMonitor:
    def test_tail_events_and_signals(self, tmp_path):
        """统一协议三种形态（state / event+tool / state 收尾）→ 信号正确。"""
        app = QApplication.instance() or QApplication([])
        events = tmp_path / "sub" / "gemini.jsonl"
        events.parent.mkdir(parents=True)
        events.touch()

        states, tools = [], []
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(events))
        mon.state_changed.connect(lambda k, s: states.append((k, s)))
        mon.activity.connect(lambda k, t: tools.append((k, t)))
        mon.start()
        mon._poll()  # backfill 初始化

        with open(events, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 1.0, "state": "working"}) + "\n")
            f.write(json.dumps({"ts": 2.0, "event": "PreToolUse", "tool": "bash"}) + "\n")
            f.write(json.dumps({"ts": 3.0, "state": "idle"}) + "\n")

        mon._poll()
        # PreToolUse 事件按内置映射同时产生 working 状态 + bash 工具过程
        assert states == [("gemini", "working"), ("gemini", "working"), ("gemini", "idle")]
        assert tools == [("gemini", "bash")]
        mon.stop()

    def test_missing_file_idle_then_appears(self, tmp_path):
        """文件不存在时空转；出现后 backfill 防护跳过历史，只读新增行。"""
        app = QApplication.instance() or QApplication([])
        missing = tmp_path / "not_yet.jsonl"
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(missing))
        states = []
        mon.state_changed.connect(lambda k, s: states.append((k, s)))
        mon.start()
        mon._poll()
        mon._poll()
        assert states == []

        missing.write_text('{"state": "working"}\n', encoding="utf-8")
        mon._poll()  # 首次发现文件：backfill，不回放历史
        assert states == []

        with open(missing, "a", encoding="utf-8") as f:
            f.write('{"state": "idle"}\n')
        mon._poll()
        assert states == [("gemini", "idle")]
        mon.stop()

    def test_start_does_not_create_dirs(self, tmp_path):
        """只读监听：绝不替用户在任意路径创建目录。"""
        app = QApplication.instance() or QApplication([])
        mon = CustomAgentMonitor(
            "gemini", tmp_path / "cfg", str(tmp_path / "deep" / "nested" / "ev.jsonl"),
        )
        mon.start()
        mon._poll()
        assert not (tmp_path / "deep").exists()
        mon.stop()

    def test_tilde_path_expanded(self, tmp_path, monkeypatch):
        # expanduser 在 Windows 读 USERPROFILE、POSIX 读 HOME，两个都设以保证跨平台
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", "~/events.jsonl")
        assert mon.events_file == tmp_path / "events.jsonl"
        assert "~" not in str(mon.events_file)


class TestCustomAgentManager:
    def test_registered_names_merged_and_generic_toggle(self, tmp_path):
        """custom_agents → 监视器注册 + 显示名合并 + 通用开关联动（无需授权弹窗）。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [
            {"key": "gemini", "name": "Gemini CLI", "path": str(tmp_path / "gemini.jsonl")},
        ]
        cfg.set("agent_link", ag)
        cfg.save()

        mgr = AgentLinkManager(None, cfg)
        assert "gemini" in mgr.monitors
        assert isinstance(mgr.monitors["gemini"], CustomAgentMonitor)
        assert mgr.agent_names["gemini"] == "Gemini CLI"
        # 类级 AGENT_NAMES 保持仅内置：设置页按内置枚举的遍历不受自定义影响
        assert "gemini" not in AgentLinkManager.AGENT_NAMES
        assert mgr.agent_names["dsh"] == "DSH"

        # 通用开关：开启持久化并启动监视器
        assert mgr.set_enabled("gemini", True) is True
        assert cfg.data["agent_link"]["gemini"] is True
        assert mgr.monitors["gemini"].is_running() is True

        # 隐藏暂停 / 显示恢复
        mgr.pause()
        assert mgr.monitors["gemini"].is_running() is False
        mgr.resume()
        assert mgr.monitors["gemini"].is_running() is True

        # 关闭
        assert mgr.set_enabled("gemini", False) is True
        assert cfg.data["agent_link"]["gemini"] is False
        assert mgr.monitors["gemini"].is_running() is False

    def test_builtin_key_in_custom_agents_ignored(self, tmp_path):
        """config 清洗会拒绝与内置键冲突的自定义条目，管理器不覆盖内置监视器。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [{"key": "claude", "name": "Fake", "path": str(tmp_path / "x.jsonl")}]
        cfg.set("agent_link", ag)
        cfg.save()

        mgr = AgentLinkManager(None, cfg)
        assert not isinstance(mgr.monitors["claude"], CustomAgentMonitor)
        assert mgr.agent_names["claude"] == "Claude Code"


class TestCustomAgentMenu:
    def test_menu_lists_custom_agent_and_toggle_routes(self, tmp_path):
        """右键菜单动态渲染自定义 Agent，勾选走通用 _toggle_agent_link。"""
        from PySide6.QtWidgets import QMenu
        from pet.context_menus.shared import add_agent_link_menu

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [
            {"key": "gemini", "name": "Gemini CLI", "path": "~/gemini.jsonl"},
        ]
        cfg.set("agent_link", ag)
        cfg.save()

        toggles, options = [], []

        class DummyPet:
            def __init__(self):
                self.cfg = cfg

            def toggle_agent_link(self, key, on, action=None):
                toggles.append((key, on))

            def set_agent_link_option(self, key, on):
                options.append((key, on))

            _toggle_agent_link = toggle_agent_link
            _set_agent_link_option = set_agent_link_option

        menu = QMenu()
        try:
            add_agent_link_menu(menu, DummyPet())
            sub = menu.actions()[0].menu()
            texts = [a.text() for a in sub.actions()]
            # 内置 4 项仍在，自定义项按显示名插入
            for label in ("DeepSeek Harness (DSH)", "Claude Code", "Cursor", "OpenCode"):
                assert label in texts
            assert "Gemini CLI" in texts
            # Agent 联动子菜单不再带「台词风格」「循环检测/卡住检测」入口——
            # 检测类配置已收敛到设置页（自动化与联动），仅保留联动相关设置
            assert "台词风格" not in texts

            gemini_act = next(a for a in sub.actions() if a.text() == "Gemini CLI")
            gemini_act.setChecked(True)
            assert toggles == [("gemini", True)]
        finally:
            import shiboken6
            shiboken6.delete(menu)


# ============================================================================
# 阻塞型交互气泡生命周期（审批 / 用户问题统一处理，一直挂到 resolved）
# ============================================================================
class TestApprovalStickyBubble:
    """阻塞型交互气泡永久挂着：approval/request、question/requested → sticky；
    decided / resolved / idle / offline → 消失。

    覆盖：sticky 展示、resolved 收尾、并发交互互不覆盖、idle 兜底、全量清除、
    _saw_alert 补记（完成后不误说"干完活啦"）、question 选项排版。
    """

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self._sticky_bubble_active = False
                self.sticky_shown: list[tuple[str, bool]] = []
                self.hidden_calls = 0
                self._alert_current = None
                self._alert_queue = []

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self._sticky_bubble_active = bool(sticky)
                self.sticky_shown.append((str(text), bool(sticky)))
                if buttons:
                    self.shown_buttons.append((str(text), [item for pair in buttons for item in (pair if pair[0] in (SECTION_HEADER_LABEL, SECTION_HINT_LABEL) else (pair[0],))]))

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True, alert_id=""):
                self._alert_queue.append({"id": alert_id, "text": str(text), "sticky": sticky})
                if self._alert_current is None and self._alert_queue:
                    self._alert_current = self._alert_queue.pop(0)
                    self._sticky_bubble_active = self._alert_current.get("sticky", True)
                self.sticky_shown.append((str(text), sticky))
                if buttons:
                    self.shown_buttons.append((str(text), [item for pair in buttons for item in (pair if pair[0] in (SECTION_HEADER_LABEL, SECTION_HINT_LABEL) else (pair[0],))]))

            def resolve_alert(self, alert_id):
                if self._alert_current and self._alert_current.get("id") == alert_id:
                    self._alert_current = None
                    self.hidden_calls += 1
                    self._sticky_bubble_active = False
                    if self._alert_queue:
                        self._alert_current = self._alert_queue.pop(0)
                        self._sticky_bubble_active = self._alert_current.get("sticky", True)
                else:
                    self._alert_queue = [q for q in self._alert_queue if q.get("id") != alert_id]

            def hide_bubble(self):
                self.hidden_calls += 1
                self._sticky_bubble_active = False
                self._alert_current = None

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        mgr.win.shown_buttons = []
        return mgr

    def _single_pending(self, mgr, agent_key: str) -> dict:
        """取该 agent 唯一一条 pending 交互（多条时断言失败，供单交互测试用）。"""
        items = mgr.pending_interactions_for(agent_key)
        assert len(items) == 1, f"期望 {agent_key} 只有一条 pending，实际 {len(items)} 条"
        return next(iter(items.values()))

    def _single_iid(self, mgr, agent_key: str) -> str:
        items = mgr.pending_interactions_for(agent_key)
        assert len(items) == 1
        return next(iter(items))

    def _agent_keys(self, mgr) -> set:
        return {item.get("agent_key") for item in mgr._pending_interactions.values()}

    def test_approval_request_shows_sticky(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-hint", "sessionId": "s-1"})
        assert self._agent_keys(mgr) == {"dsh"}
        pending = self._single_pending(mgr, "dsh")
        assert pending["kind"] == "approval"
        assert pending["interactive"] is False
        assert mgr.win._sticky_bubble_active is True
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        # legacy 内置预设 approval.tool 首句：请求使用工具名（原 fallback 含「审批」字样）
        assert "请求使用工具" in text and "bash" in text
        assert mgr.win.shown_buttons == [], "无 rpcId 时不得出按钮（纯提示）"

    def test_approval_request_shows_full_command(self, tmp_path):
        """审批气泡必须展示被审批命令的完整内容（来自 bridge 的 command 字段）。"""
        mgr = self._make_mgr(tmp_path)
        cmd = "pip install pytest --index-url http://mirrors.aliyun.com/pypi/simple/"
        mgr._on_approval_request("dsh", {"tool": "bash", "command": cmd, "approvalId": "ap-cmd", "sessionId": "s-1"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert cmd in text, "气泡文案必须包含命令完整内容"
        assert self._single_pending(mgr, "dsh")["command"] == cmd

    def test_approval_request_formats_command_single_line(self, tmp_path):
        """多行/多空格命令折叠成单行展示（气泡图片不保留换行）。"""
        mgr = self._make_mgr(tmp_path)
        raw = "pip install pytest\n\n  --index-url http://example.com/simple/\n"
        mgr._on_approval_request("dsh", {"tool": "bash", "command": raw, "approvalId": "ap-raw", "sessionId": "s-1"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "\n" not in text, "换行必须折叠成空格"
        assert "pip install pytest --index-url http://example.com/simple/" in text

    def test_approval_request_truncates_overlong_command(self, tmp_path):
        """超长命令截断并加省略号，避免撑爆气泡。"""
        mgr = self._make_mgr(tmp_path)
        long_cmd = "x" * 500
        mgr._on_approval_request("dsh", {"tool": "bash", "command": long_cmd, "approvalId": "ap-long", "sessionId": "s-1"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "…" in text
        assert "x" * 500 not in text

    def test_approval_request_command_missing_falls_back_to_tool(self, tmp_path):
        """无 command 字段时回退到工具名文案（兼容旧桥接路径）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "write", "approvalId": "ap-w", "sessionId": "s-1"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "请求执行" not in text
        assert "请求使用工具" in text

    def test_approval_resolved_dismisses(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-r", "sessionId": "s-1"})
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-r"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_concurrent_approvals_resolved_last(self, tmp_path):
        """两个 agent 并发审批：各自 pending；队列模型下逐条关闭并推进下一条。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-d", "sessionId": "s-1"})
        mgr._on_approval_request("claude", {"tool": "write", "approvalId": "ap-c", "sessionId": "s-2"})
        assert self._agent_keys(mgr) == {"dsh", "claude"}
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-d"})
        assert self._agent_keys(mgr) == {"claude"}
        assert mgr.win._sticky_bubble_active is True, "dsh 审批关闭后 claude 审批顶上，气泡仍挂着"
        mgr._on_approval_resolved("claude", {"approvalId": "ap-c"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 2
        assert mgr.win._sticky_bubble_active is False

    def test_idle_does_not_dismiss_approval(self, tmp_path):
        """agent 短暂回 idle（step 间隙/空闲心跳）不得兜底清掉未决审批。

        回归背景：DSH 的 AgentStatus idle 每 2-5s 出现（working→idle→working），
        旧实现把 idle 当"任务结束"清 pending，导致仍在等待用户决定的审批/选择
        常驻气泡被误清（用户报告"审批/选择失去常驻显示直到交互完成"）。
        现在 idle 不再清：未决审批由 approval/resolved 权威帧精确关闭，
        兜底只走 _INTERACTION_END_EVENTS（turn/end 等）与 dismiss_all。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-i", "sessionId": "s-1"})
        assert mgr._pending_interactions, "审批应先登记"
        mgr._on_agent_state("dsh", "idle")
        assert mgr._pending_interactions, "idle 不得清掉未决审批"
        assert mgr.win.hidden_calls == 0, "idle 不得主动隐藏气泡"
        # 权威 resolved 帧正常关闭。
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-i"})
        assert mgr._pending_interactions == {}

    def test_dismiss_all_approvals(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-all", "sessionId": "s-1"})
        mgr.dismiss_all_approvals()
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_approval_records_saw_alert(self, tmp_path):
        """审批打断算"需要主人看一眼"：完成后不误说"干完活啦"。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-saw", "sessionId": "s-1"})
        assert "dsh" in mgr._saw_alert

    def test_resolved_unknown_agent_noop(self, tmp_path):
        """没有对应 pending 的 resolved 事件是空操作，不误关气泡。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_resolved("dsh")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 0

    def test_approval_resolved_call_id_does_not_close_question(self, tmp_path):
        """审批 resolved 帧带 callId 时不得按 callId 关闭问题交互。

        `_on_approval_resolved` 的 callId 分支是从问题侧复制粘贴来的错位判定：
        approval 与 question 的 callId 是两个独立命名空间，若该分支按
        kind == "question" 遍历，一条无关审批的收尾帧就会把同名 callId 的
        问题气泡误关掉（用户还没回答，问题弹窗先消失）。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "callId": "call-shared"}
        )

        mgr._on_approval_resolved("dsh", {"callId": "call-shared"})

        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 1, "审批 resolved 不得误关同名 callId 的问题气泡"
        assert next(iter(pending.values()))["kind"] == "question"
        assert mgr.win.hidden_calls == 0

    def test_approval_resolved_call_id_closes_approval(self, tmp_path):
        """审批 resolved 帧带 callId 时按 callId 关闭审批交互。

        登记端必须存下审批的 callId 身份，否则改判 kind 后新分支也无从匹配。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "callId": "call-ap", "sessionId": "s-1"}
        )
        assert mgr.pending_interactions_for("dsh") != {}

        mgr._on_approval_resolved("dsh", {"callId": "call-ap"})

        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.hidden_calls == 1

    # ---- 用户问题（ask_user_question）与审批同待遇 ----
    QUESTIONS = [
        {"id": "q1", "question": "要执行哪个方案？",
         "options": [{"label": "方案 A"}, {"label": "方案 B"}, {"label": "方案 C"}],
         "multiSelect": False},
    ]

    def test_question_request_shows_sticky_with_options(self, tmp_path):
        """question/requested 带 options：常驻气泡列出选项，让用户选一个才能继续。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-q1"})
        pending = mgr.pending_interactions_for("dsh")
        assert pending, "应有至少一条 pending 交互"
        item = next(iter(pending.values()))
        assert item["kind"] == "question"
        assert item["interactive"] is False
        assert mgr.win._sticky_bubble_active is True
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "要执行哪个方案" in text
        assert "方案 A" in text and "方案 B" in text and "方案 C" in text
        assert "请选择一个" in text
        assert mgr.win.shown_buttons == [], "无 rpcId 时不得出按钮（纯提示）"

    def test_question_resolved_dismisses(self, tmp_path):
        """question/resolved → 气泡收尾。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-qr"})
        mgr._on_question_resolved("dsh", {"callId": "call-qr"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_question_resolved_matches_call_id_with_multiple_pending(self, tmp_path):
        """并发问题必须按 callId 关闭，不能因无 rpcId 而让整个提醒队列卡住。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": self.QUESTIONS, "callId": "call-a", "sessionId": "session-a",
        })
        mgr._on_question_request("dsh", {
            "questions": self.QUESTIONS, "callId": "call-b", "sessionId": "session-a",
        })

        assert len(mgr.pending_interactions_for("dsh")) == 2
        mgr._on_question_resolved("dsh", {"callId": "call-b", "sessionId": "session-a"})

        assert len(mgr.pending_interactions_for("dsh")) == 1
        remaining = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert remaining["call_id"] == "call-a"

    def test_pending_interaction_uses_interaction_id_not_agent_key(self, tmp_path):
        """pending_interactions 的键是 interaction_id 而非 agent_key。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-x"})
        keys = list(mgr._pending_interactions.keys())
        assert keys, "应有至少一条 pending 交互"
        assert "dsh" not in keys, "键应为 interaction_id，不是 agent_key"
        assert "rpc-x" in keys[0], f"键应包含 rpcId（如 approval:rpc-x），实际为 {keys[0]}"
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 1
        item = next(iter(pending.values()))
        assert item["agent_key"] == "dsh"
        assert item["kind"] == "question"

    def test_concurrent_pending_resolved_independently(self, tmp_path):
        """同一个 Agent 有两个 pending interaction → 解决其中一个，另一个仍然存在。

        并发两个审批后分别解决一个，验证未解决的审批不会因另一个解决而关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-a", "approvalId": "ap-a", "sessionId": "s-1"}
        )
        mgr._on_approval_request(
            "dsh", {"tool": "pwsh", "rpcId": "rpc-b", "approvalId": "ap-b", "sessionId": "s-1"}
        )
        pending_before = mgr.pending_interactions_for("dsh")
        assert len(pending_before) == 2, f"应有 2 条 pending 交互，实际 {len(pending_before)}"
        pending_keys = set(pending_before)
        assert "approval:rpc-a" in pending_keys and "approval:rpc-b" in pending_keys

        # 解决 A
        mgr._respond_interaction("approval:rpc-a", "allowed-once")
        remaining = mgr.pending_interactions_for("dsh")
        assert len(remaining) == 1, "解决 A 后应只剩 B"
        assert "approval:rpc-b" in remaining, "B 仍应处于 pending 状态"
        item_b = remaining["approval:rpc-b"]
        assert item_b["tool"] == "pwsh"
        assert item_b["approval_id"] == "ap-b"

        # 解决 B 后全部清空
        mgr._respond_interaction("approval:rpc-b", "allowed-once")
        assert mgr.pending_interactions_for("dsh") == {}, "解决 B 后应全部清空"

    def test_question_no_options_needs_input(self, tmp_path):
        """无 options 的问题（自由输入/确认）：提示需要输入，不出交互按钮。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "q2", "question": "请补充上下文"}], "rpcId": "rpc-free"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "请补充上下文" in text
        assert "正在询问" in text
        # 无选项=自由输入：即使 preset 文案被覆盖，结构引导也必须保留
        assert "请到 DSH 界面输入文本回答" in text
        assert mgr.win.shown_buttons == [], "自由输入问题不出可点按钮"

    def test_question_multi_question(self, tmp_path):
        """一次多个问题：提示有几个问题等你回答。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "a", "question": "Q1"}, {"id": "b", "question": "Q2"}], "callId": "call-multi"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "2 个问题" in text

    def test_question_and_approval_independent(self, tmp_path):
        """并发一个审批 + 一个问题：各自独立 pending，不再互相覆盖；分别 resolved 后全部关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-ind", "sessionId": "s-1"})
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-ind"})
        # 同一 agent 的多个交互各自独立存储（不再互相覆盖）
        assert self._agent_keys(mgr) == {"dsh"}
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 2, "审批和问题应共存，各自一条 pending"
        # 审批和问题各自有 kind
        kinds = {item["kind"] for item in pending.values()}
        assert kinds == {"approval", "question"}
        # 分别 resolved：先关闭问题
        mgr._on_question_resolved("dsh", {"callId": "call-ind"})
        assert len(mgr.pending_interactions_for("dsh")) == 1, "问题关闭后审批还在"
        assert self._single_pending(mgr, "dsh")["kind"] == "approval"
        # 再关闭审批
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-ind"})
        assert mgr.pending_interactions_for("dsh") == {}

    # ---- 交互模式（带 rpcId，气泡内可直接点选） ----

    def test_approval_interactive_buttons(self, tmp_path):
        """审批带 rpcId：气泡内嵌「同意/拒绝」两个按钮。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-1", "approvalId": "ap-1", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert item["rpc_id"] == "rpc-1"
        assert item["approval_id"] == "ap-1"
        assert item["session_id"] == "s-1"
        assert mgr.win.shown_buttons and mgr.win.shown_buttons[-1][1] == ["同意", "拒绝"]

    def test_question_interactive_buttons(self, tmp_path):
        """问题带 rpcId + options：气泡内嵌每个选项的按钮。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-2", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert mgr.win.shown_buttons and mgr.win.shown_buttons[-1][1] == ["方案 A", "方案 B", "方案 C"]

    def test_question_multi_branch_grouped_buttons(self, tmp_path):
        """多分支问题：一个多路弹窗按分支分组展示（各分支标题 + 自己的选项），
        末尾一个「提交回答」一次性回写全部 answers——不把全部分支选项平铺成一列。"""
        mgr = self._make_mgr(tmp_path)
        questions = [
            {"id": "dA", "question": "双分支第一问：读取哪个文件？", "header": "分支 A",
             "options": [{"label": "alpha"}, {"label": "beta"}], "multiSelect": False},
            {"id": "dB", "question": "双分支第二问：执行哪个命令？", "header": "分支 B",
             "options": [{"label": "ls"}, {"label": "time"}], "multiSelect": False},
        ]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-multi", "sessionId": "s-1"})
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert item["interactive"] is True
        labels = mgr.win.shown_buttons[-1][1]
        # 分支 A 标题 + 其选项；分支 B 标题 + 其选项；末尾提交
        assert labels == [
            SECTION_HEADER_LABEL, "分支 A", "alpha", "beta",
            SECTION_HEADER_LABEL, "分支 B", "ls", "time",
            "提交回答",
        ]

    def test_question_multi_branch_no_flat_option_soup(self, tmp_path):
        """多分支问题不应把两个分支的选项平铺成同一列（回归保护）。"""
        mgr = self._make_mgr(tmp_path)
        questions = [
            {"id": "dA", "question": "Q A", "header": "分支 A", "options": [{"label": "a1"}, {"label": "a2"}]},
            {"id": "dB", "question": "Q B", "header": "分支 B", "options": [{"label": "b1"}, {"label": "b2"}]},
        ]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-9", "sessionId": "s-1"})
        labels = mgr.win.shown_buttons[-1][1]
        # 分支 A 的选项必须紧跟「分支 A」标题之后，而不是被平铺混排
        first_branch = labels[labels.index(SECTION_HEADER_LABEL) + 1: labels.index(SECTION_HEADER_LABEL, labels.index(SECTION_HEADER_LABEL) + 1)]
        assert first_branch == ["分支 A", "a1", "a2"], f"分支 A 应自成一组，实际 {first_branch}"

    def test_question_multi_branch_with_free_text_is_hint(self, tmp_path):
        """多分支事件里任一分支是自由文本（无 options）：气泡内无法收集文本，
        提交按钮被隐藏，整个批次按完整整体回落到 DSH 界面输入文本回答。"""
        mgr = self._make_mgr(tmp_path)
        questions = [
            {"id": "dA", "question": "分支 A 选择", "options": [{"label": "ok"}]},
            {"id": "dB", "question": "分支 B 需要文本", "options": []},
        ]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-mixed", "sessionId": "s-1"})
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert item["interactive"] is False, "含自由文本分支时整批不可气泡内交互"
        assert mgr.win.shown_buttons == [], "自由文本分支时不得出提交/选项按钮（隐藏）"
        text, sticky = mgr.win.sticky_shown[-1]
        assert "2 个问题" in text
        assert "DSH 界面输入文本回答" in text, f"应有回到 DSH 界面输入的提示，实际 {text!r}"

    def test_question_no_options_needs_input_text_mentions_dsh(self, tmp_path):
        """单个自由文本问题：纯提示气泡，文案明确引导回 DSH 界面输入文本。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "q2", "question": "请补充上下文"}], "rpcId": "rpc-free2"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "请补充上下文" in text
        assert "正在询问" in text
        assert "请到 DSH 界面输入文本回答" in text
        assert mgr.win.shown_buttons == []

    def test_hint_upgraded_to_interactive(self, tmp_path):
        """先到无 rpcId 的提示（带 callId），后到带 rpcId 的同款交互：升级为可点选气泡。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-up"})
        assert mgr.win.shown_buttons == []
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-3", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert mgr.win.shown_buttons[-1][1] == ["方案 A", "方案 B", "方案 C"]

    def test_hint_upgrade_keeps_call_id(self, tmp_path):
        """升级重建保留旧 callId：hint 带 callId → 交互版升级 → 兜底 resolved 仍能关闭。

        桥接双通道的真实形状：tool/call 兜底记录带 callId，随后 mux 交互帧只带
        rpcId（不带 callId）。升级重建若把 call_id 覆盖成 None，mux 断线时兜底
        发出的 question/resolved(callId) 就再也匹配不上，气泡永久挂住。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-keep"})
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-keep", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 1, "同一条问题只应有一条 pending"
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert item["rpc_id"] == "rpc-keep"
        assert item["call_id"] == "call-keep", "升级重建不得丢掉旧 callId"
        mgr._on_question_resolved("dsh", {"callId": "call-keep"})
        assert mgr.pending_interactions_for("dsh") == {}, "兜底 callId 关闭必须仍然有效"

    def test_upgrade_empty_string_does_not_clear_call_id(self, tmp_path):
        """升级帧显式带空串 callId（桥接 String(...) || \"\" 兜底形状）不得清掉旧身份。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-keep"})
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-keep", "sessionId": "s-1", "callId": ""}
        )
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert item["call_id"] == "call-keep", "空串 callId 不得覆盖旧身份"

    def test_interactive_not_downgraded_by_late_hint(self, tmp_path):
        """先到带 rpcId 的交互版，后到无 rpcId 的提示→不降级，仍保持可点选。

        与 test_hint_upgraded_to_interactive 对称：反向竞态下，后到的无 rpcId
        记录不得把已有的交互 pending 降级为纯提示，否则按钮会丢失绑定。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-1", "approvalId": "ap-1", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert item["rpc_id"] == "rpc-1"
        assert mgr.win.shown_buttons[-1][1] == ["同意", "拒绝"]

        # 后到无 rpcId 的提示（带 approvalId）：不降级
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-1", "sessionId": "s-1"})
        pending2 = mgr.pending_interactions_for("dsh")
        item2 = next(iter(pending2.values()))
        assert item2["interactive"] is True, "不应降级为纯提示"
        assert item2["rpc_id"] == "rpc-1", "rpc_id 应保留"
        assert mgr.win.shown_buttons[-1][1] == ["同意", "拒绝"], "按钮应仍然存在"

    def test_build_respond_approval_message(self, tmp_path):
        """审批点「同意」→ client-response 载荷形状与 web UI 一致。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-1", "approvalId": "ap-1", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        msg = mgr._build_respond_message(item, "allowed-once")
        assert msg == {
            "type": "client-response",
            "rpcId": "rpc-1",
            "result": {"ok": True, "value": {"sessionId": "s-1", "approvalId": "ap-1", "outcome": "allowed-once"}},
        }

    def test_stale_resolved_does_not_close_next_approval(self, tmp_path):
        """用户点 A 后，DSH 延迟回发的 A 的 resolved/decided 不得误关仍在等待的 B。

        时序：点 A → A 本地 resolve 并回写 → DSH 处理完回发 A 的 resolved（带
        rpcId）与 decided（无 id）→ 若此时按「仅剩一条 pending」兜底关闭，
        B 会被误关（弹窗延迟 0.5~1s 消失，DSH 却只收到 A 的决策）。
        带 id 的帧未匹配到 pending 即陈旧已解决帧，不兜底；无 id 的旧路径
        decided 只对无 rpc_id 的纯提示兜底，不碰带 rpc_id 的交互审批。"""
        mgr = self._make_mgr(tmp_path)
        posted = []
        mgr._post_respond_worker = lambda agent_key, msg: posted.append((agent_key, msg))
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-A", "approvalId": "ap-A", "sessionId": "s-1"}
        )
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-B", "approvalId": "ap-B", "sessionId": "s-1"}
        )
        assert set(mgr.pending_interactions_for("dsh")) == {"approval:rpc-A", "approval:rpc-B"}

        # 点 A
        mgr._respond_interaction("approval:rpc-A", "allowed-once")
        assert set(mgr.pending_interactions_for("dsh")) == {"approval:rpc-B"}
        assert posted and posted[0][1]["rpcId"] == "rpc-A"

        # DSH 回发 A 的 resolved（mux，带 id）+ decided（session，无 id）
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-A", "approvalId": "ap-A", "outcome": "allowed-once"})
        mgr._on_approval_resolved("dsh", {})
        assert set(mgr.pending_interactions_for("dsh")) == {"approval:rpc-B"}, \
            "A 的陈旧 resolved/decided 不得误关 B"
        assert mgr.win.hidden_calls == 1, f"只应 resolve A 一次，实际 {mgr.win.hidden_calls}"

        # 点 B 正常收尾，回写 A、B 各一次
        mgr._respond_interaction("approval:rpc-B", "allowed-once")
        assert mgr.pending_interactions_for("dsh") == {}
        assert [m[1]["rpcId"] for m in posted] == ["rpc-A", "rpc-B"]

    def test_build_respond_question_message(self, tmp_path):
        """问题点「方案 B」→ selected=[该选项 label] 的载荷形状。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-2", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        msg = mgr._build_respond_message(item, ["方案 B"])
        assert msg["rpcId"] == "rpc-2"
        assert msg["result"]["ok"] is True
        value = msg["result"]["value"]
        assert value["sessionId"] == "s-1"
        assert value["answer"]["answers"] == [{"id": "q1", "selected": ["方案 B"]}]

    def test_build_respond_question_message_all_questions_and_multiselect(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        questions = [
            {"id": "q1", "question": "分支", "options": [{"label": "A"}, {"label": "B"}], "multiSelect": True},
            {"id": "q2", "question": "模式", "options": [{"label": "计划"}], "multiSelect": False},
        ]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "question-rpc", "sessionId": "session-1"})
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        msg = mgr._build_respond_message(item, {"answers": [
            {"id": "q1", "selected": ["A", "B"]},
            {"id": "q2", "selected": ["计划"]},
        ]})
        assert msg["result"]["value"]["answer"]["answers"] == [
            {"id": "q1", "selected": ["A", "B"]},
            {"id": "q2", "selected": ["计划"]},
        ]

    def test_question_payload_keeps_custom_and_intent_per_question(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        questions = [{"id": "q1", "question": "评审", "intent": {"kind": "plan-review"}}, {"id": "q2", "question": "补充", "options": []}]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-custom", "sessionId": "s-custom"})
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert item["questions"] == questions
        msg = mgr._build_respond_message(item, {"answers": [{"id": "q1", "selected": ["继续"]}, {"id": "q2", "selected": [], "custom": "补充内容"}]})
        assert msg["result"]["value"]["answer"]["answers"][1]["custom"] == "补充内容"

    def test_question_resolved_matches_session_and_question_rpc_id(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        for session, rpc in (("s1", "r1"), ("s2", "r2")):
            mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "rpcId": rpc, "sessionId": session})
        mgr._on_question_resolved("dsh", {"rpcId": "r1", "sessionId": "s1"})
        assert set(mgr.pending_interactions_for("dsh")) == {"question:r2"}
    def test_build_respond_without_rpcid_is_none(self, tmp_path):
        """仅带 approvalId（无 rpcId）的纯提示交互：没有可回写消息（返回 None）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-h", "sessionId": "s-1"})
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert mgr._build_respond_message(item, "allowed-once") is None

    def test_respond_interaction_posts_worker(self, tmp_path):
        """点按钮触发回写：收起 pending + 起后台线程带正确消息。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-1", "approvalId": "ap-1", "sessionId": "s-1"}
        )
        captured = {}
        mgr._post_respond_worker = lambda agent_key, msg: captured.update({"agent": agent_key, "msg": msg})
        mgr._respond_interaction("approval:rpc-1", "rejected")
        assert mgr.pending_interactions_for("dsh") == {}, "点击后 pending 立即收起"
        assert captured["agent"] == "dsh"
        assert captured["msg"]["result"]["value"]["outcome"] == "rejected"
        assert mgr.win.hidden_calls == 1


# ============================================================================
# 阻塞交互身份门禁（防普通工具调用 / 审计事件误触发审批弹窗）
# ============================================================================
class TestInteractionIdentityGate:
    """Get-Location 等普通工具调用、无身份的审计事件绝不能被升级成审批弹窗。

    覆盖：
    - approval 无任何可关联身份（rpcId/approvalId/requestId/callId）→ 不弹窗；
    - 仅带 approvalId（无 rpcId）的真实兼容路径 → 纯提示且可被身份 resolved 关闭；
    - monitor 层：裸 approval/asked 无论带不带身份都不触发审批信号；
    - monitor 层：普通 tool/call（pwsh Get-Location）只发 activity，绝不触发审批；
    - question 无 rpcId 也无 callId → 不弹窗；带 callId 的兜底路径仍可提示并关闭；
    - cordis 仅严格布尔 requiresApproval=True 且带 requestId 才触发；
    - turn 结束兜底清理：漏发 resolved 时不留永久弹窗，且只清对应会话。
    """

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self._sticky_bubble_active = False
                self.hidden_calls = 0
                self._alert_current = None
                self._alert_queue = []
                self.shown_buttons = []
                self.sticky_shown = []

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self._sticky_bubble_active = bool(sticky)
                self.sticky_shown.append((str(text), bool(sticky)))
                if buttons:
                    self.shown_buttons.append((str(text), [item for pair in buttons for item in (pair if pair[0] in (SECTION_HEADER_LABEL, SECTION_HINT_LABEL) else (pair[0],))]))

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True, alert_id=""):
                self._alert_queue.append({"id": alert_id, "text": str(text), "sticky": sticky})
                if self._alert_current is None and self._alert_queue:
                    self._alert_current = self._alert_queue.pop(0)
                    self._sticky_bubble_active = self._alert_current.get("sticky", True)
                self.sticky_shown.append((str(text), sticky))
                if buttons:
                    self.shown_buttons.append((str(text), [item for pair in buttons for item in (pair if pair[0] in (SECTION_HEADER_LABEL, SECTION_HINT_LABEL) else (pair[0],))]))

            def resolve_alert(self, alert_id):
                if self._alert_current and self._alert_current.get("id") == alert_id:
                    self._alert_current = None
                    self.hidden_calls += 1
                    self._sticky_bubble_active = False
                    if self._alert_queue:
                        self._alert_current = self._alert_queue.pop(0)
                        self._sticky_bubble_active = self._alert_current.get("sticky", True)
                else:
                    self._alert_queue = [q for q in self._alert_queue if q.get("id") != alert_id]

            def hide_bubble(self):
                self.hidden_calls += 1
                self._sticky_bubble_active = False
                self._alert_current = None

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        mgr.win.shown_buttons = []
        return mgr

    def _make_mon(self, tmp_path):
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._tailer.read_new_lines()  # backfill 初始化
        return mon

    def _write_events(self, mon, events):
        with mon.events_file.open("a", encoding="utf-8") as fh:
            for line in events:
                fh.write(json.dumps(line) + "\n")
        mon._poll()

    @pytest.mark.parametrize("command", [
        "Get-Location", "Get-ChildItem", "pwd", "ls", "git status",
        "Get-Location | Select-Object -ExpandProperty Path",
    ])
    def test_approval_without_identity_ignored(self, tmp_path, command):
        """无任何可关联身份的审批记录（普通工具调用被误标 approval/asked 后的残留）不弹窗。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "pwsh", "command": command})
        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.sticky_shown == []
        assert mgr.win._sticky_bubble_active is False

    def test_approval_with_only_approval_id_is_hint_and_closable(self, tmp_path):
        """仅带 approvalId（无 rpcId）的真实兼容路径：显示纯提示，且可被身份 resolved 精确关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-x", "sessionId": "s-1"})
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "approval"
        assert item["interactive"] is False
        assert item["approval_id"] == "ap-x"
        assert mgr.win.shown_buttons == [], "无 rpcId 时不得出按钮（纯提示）"
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-x"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_approval_asked_never_emits_request_signal(self, tmp_path):
        """monitor 层：裸 approval/asked 无论带不带身份，都绝不触发审批弹窗信号。"""
        mon = self._make_mon(tmp_path)
        got = []
        mon.approval_requested.connect(lambda a, p: got.append((a, p.get("event"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "approval/asked", "tool": "pwsh", "command": "Get-Location"},
            {"ts": 2, "agent": "dsh", "event": "approval/asked", "approvalId": "ap-a", "sessionId": "s-1"},
            {"ts": 3, "agent": "dsh", "event": "approval/asked", "rpcId": "rpc-a", "sessionId": "s-1"},
        ])
        assert got == [], "approval/asked 不应驱动审批弹窗信号"

    def test_approval_requested_still_emits_request_signal(self, tmp_path):
        """monitor 层：权威 approval/request 与兼容旧名 approval/requested 正常触发审批信号。"""
        mon = self._make_mon(tmp_path)
        got = []
        mon.approval_requested.connect(lambda a, p: got.append((a, p.get("event"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "approval/request", "rpcId": "r1", "sessionId": "s1"},
            {"ts": 2, "agent": "dsh", "event": "approval/requested", "rpcId": "r2", "sessionId": "s1"},
        ])
        assert got == [("dsh", "approval/request"), ("dsh", "approval/requested")]

    def test_tool_call_never_becomes_approval(self, tmp_path):
        """monitor 层：普通 tool/call（pwsh Get-Location）只发 activity，绝不发审批信号。"""
        mon = self._make_mon(tmp_path)
        approvals, activities = [], []
        mon.approval_requested.connect(lambda a, p: approvals.append((a, p)))
        mon.activity.connect(lambda a, t: activities.append((a, t)))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "tool/call", "tool": "pwsh", "command": "Get-Location"},
        ])
        assert approvals == [], "普通工具调用不得触发审批"
        assert ("dsh", "pwsh") in activities

    def test_question_without_identity_ignored(self, tmp_path):
        """question/requested 无 rpcId 也无 callId：不弹窗（无法可靠关闭）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": [{"id": "q1", "question": "选择？", "options": [{"label": "A"}]}],
        })
        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.sticky_shown == []

    def test_question_with_call_id_is_hint_and_closable(self, tmp_path):
        """question/requested 带 callId（tool/call 兜底路径）：显示纯提示且可被关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": [{"id": "q1", "question": "选择？", "options": [{"label": "A"}]}],
            "callId": "call-q", "sessionId": "s-1",
        })
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "question"
        assert item["interactive"] is False
        assert mgr.win.shown_buttons == []
        mgr._on_question_resolved("dsh", {"callId": "call-q", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_requires_strict_true_and_request_id(self, tmp_path):
        """monitor 层：cordis/request-run 只有 requiresApproval 严格布尔 True 且带 requestId 才触发。

        记录形状以桥接真实写盘为准（index.js 的 cordis/request-run 分支：
        `writeRecord({event, agentId, sessionId, kind, payload: request, requestId})`
        ——原始 request 整体嵌在 payload 下，requiresApproval 只在 payload 内，
        顶层只有 requestId/agentId/sessionId 等身份字段）；旧版/手写桩把字段
        平铺在顶层的形状仍须兼容。
        """
        mon = self._make_mon(tmp_path)
        got = []
        mon.cordis_requested.connect(lambda a, p: got.append((a, p.get("requestId"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-ok",
             "payload": {"requiresApproval": True, "requestId": "r-ok", "name": "插件", "purpose": "运行"}},
            {"ts": 2, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-no",
             "payload": {"requiresApproval": False, "requestId": "r-no"}},
            {"ts": 3, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-miss",
             "payload": {"requestId": "r-miss"}},
            {"ts": 4, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-str",
             "payload": {"requiresApproval": "true", "requestId": "r-str"}},
            {"ts": 5, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-legacy",
             "requiresApproval": True},
            # 嵌套与顶层同时存在时以嵌套为准：嵌套 False 不得被顶层残留 True 顶掉。
            {"ts": 6, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-nested-wins",
             "requiresApproval": True,
             "payload": {"requiresApproval": False, "requestId": "r-nested-wins"}},
        ])
        assert got == [("dsh", "r-ok"), ("dsh", "r-legacy")], \
            "仅严格布尔 True 且带 requestId 才触发 cordis 交互（payload 内与顶层平铺两处都认，嵌套优先）"


    def test_cordis_without_request_id_ignored(self, tmp_path):
        """_on_cordis_request 无 requestId：不登记 pending 交互。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {"name": "插件", "purpose": "运行", "requiresApproval": True})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_with_request_id_registers(self, tmp_path):
        """_on_cordis_request 带 requestId：登记 pending 并可被 resolved 关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {"name": "插件", "purpose": "运行", "requiresApproval": True, "requestId": "req-1"})
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "cordis"
        assert item["request_id"] == "req-1"
        mgr._on_cordis_resolved("dsh", {"requestId": "req-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_request_reads_nested_payload_fields(self, tmp_path):
        """_on_cordis_request 消费桥接写盘形状：名称/用途/会话从 payload 内取。

        桥接顶层不带 name/purpose，只把原始 cordis request 放进 payload；只读
        顶层会得到占位文案（"Cordis 插件 请求运行：需要你的确认"），用户看不出
        是哪条请求。顶层平铺的旧版/手写桩形状仍须兼容（见上一用例）。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {
            "requestId": "req-2", "agentId": "sess-9",
            "payload": {"requestId": "req-2", "name": "构建插件", "purpose": "执行打包脚本"},
        })
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "cordis"
        assert item["request_id"] == "req-2"
        assert item["session_id"] == "sess-9"
        assert "构建插件" in item["text"]
        assert "执行打包脚本" in item["text"]

    def test_turn_end_clears_stale_pending(self, tmp_path):
        """turn 结束兜底清理：DSH 漏发 resolved 时，会话结束不再留永久弹窗。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-z", "approvalId": "ap-z", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh")
        mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_turn_end_keeps_other_session_interaction(self, tmp_path):
        """turn 结束只清对应会话的交互，不影响其他会话并发的真实审批。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-a", "approvalId": "ap-a", "sessionId": "s-1"})
        mgr._on_approval_request("dsh", {"tool": "pwsh", "rpcId": "rpc-b", "approvalId": "ap-b", "sessionId": "s-2"})
        mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "s-1"})
        remaining = mgr.pending_interactions_for("dsh")
        assert set(remaining) == {"approval:rpc-b"}

    def test_agent_idle_does_not_clear_pending_via_lifecycle(self, tmp_path):
        """AgentStatus idle **不再**兜底清理 pending 阻塞交互。

        回归背景：DSH 的 AgentStatus idle 是空闲心跳/step 间隙（实测
        working→idle→working 每 2-5s 出现），并非回合结束。旧实现把 idle
        当 ended 清 pending，导致仍在进行的审批/选择常驻气泡被误清（用户
        报告"审批/选择能传过来但失去常驻显示直到交互完成"）。现在 idle
        不再清：审批/问题由各自 resolved 帧精确关闭，兜底只走权威
        _INTERACTION_END_EVENTS（turn/end 等）与 dismiss_all_interactions。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-idle", "approvalId": "ap-idle", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh"), "审批应先登记"
        # idle（step 间隙/空闲心跳）不得清掉 pending 审批。
        mgr._on_agent_state("dsh", "idle")
        assert mgr.pending_interactions_for("dsh"), "idle 不得清 pending 审批"
        # 权威 resolved 帧正常关闭。
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-idle"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_lifecycle_only_clears_on_authoritative_ends(self, tmp_path):
        """_on_interaction_lifecycle 只认权威结束事件，AgentStatus idle 不算。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-lc", "approvalId": "ap-lc", "sessionId": "s-1"})
        # idle/sleeping 不得触发清空。
        mgr._on_interaction_lifecycle("dsh", {"event": "AgentStatus", "state": "idle"})
        assert mgr.pending_interactions_for("dsh"), "AgentStatus idle 不得视为结束"
        mgr._on_interaction_lifecycle("dsh", {"event": "AgentStatus", "state": "sleeping"})
        assert mgr.pending_interactions_for("dsh"), "sleeping 不得视为结束"
        # turn/end 权威结束 → 清对应会话。
        mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh") == {}


# ============================================================================
# 硬失败（execution/failed）：DSH 已决定本轮不再继续，直接提醒
# ============================================================================
class TestExecutionFailed:
    """execution/failed 不经行为分析直接提醒：失败动画 + 气泡。"""

    def _make_mgr(self, tmp_path, exec_failed=True):
        class FakeWin:
            def __init__(self):
                self.shown: list[str] = []
                self.alerts: list[dict] = []
                self.anims: list[str] = []
                self._visible = True

            def isVisible(self):
                return self._visible

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self.shown.append(str(text))

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True):
                self.alerts.append({
                    "text": str(text), "sticky": bool(sticky),
                    "duration_ms": int(duration_ms),
                })

            def request_link_anim(self, anim):
                self.anims.append(str(anim))

        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        # 本类只看「硬失败直接提醒」这一条链路：exec_failed 门按参数开/关，
        # 其余门留在文件头基线（写全 8 门，避免整块替换后回落到产品默认）。
        ag["report_gates"] = _agent_gates(exec_failed=1.0 if exec_failed else 0.0)
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_retry_exhausted_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"failureType": "model_retry_exhausted", "retryExhausted": True, "retries": 4})
        assert mgr.win.alerts, "应入队失败提醒"
        assert "重试" in mgr.win.alerts[-1]["text"]  # failure.retry 预设（多次重试后仍未成功）
        assert mgr.win.alerts[-1]["sticky"] is False, "失败提醒是限时气泡（非 sticky）"

    def test_tool_failure_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"failureType": "tool_failed", "retryExhausted": False})
        assert mgr.win.alerts
        assert "工具执行失败" in mgr.win.alerts[-1]["text"]  # failure.tool 预设首句

    def test_generic_failure(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {})
        assert mgr.win.alerts
        assert "执行失败" in mgr.win.alerts[-1]["text"]  # failure.generic 预设首句

    def test_picks_fail_anim(self, tmp_path):
        """角色动作池含「失败/冒烟」类动作时选择它。"""
        mgr = self._make_mgr(tmp_path)
        mgr.win.cats = {"acts": ["待机", "失败冒烟", "写代码"]}
        anim = mgr._pick_fail_anim()
        assert anim == "失败冒烟"

    def test_exec_failed_gate_closed_no_reminder(self, tmp_path):
        """report_gates.exec_failed=0.0 时不提醒。"""
        mgr = self._make_mgr(tmp_path, exec_failed=False)
        mgr._on_execution_failed("dsh", {"failureType": "model_retry_exhausted", "retryExhausted": True})
        assert mgr.win.shown == []





class TestModelAccessAlert:
    """model_access 事件 → 高优先级提醒，合并计数，按 session 隔离，可关闭。"""

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self.alerts: list[dict] = []
                self.resolved: list[str] = []
                self.shown: list[str] = []
                self._visible = True
                # 有意不提供 _bubble_busy_until：_schedule_model_access_dismiss 会据 sentinel 跳过 QTimer

            def isVisible(self):
                return self._visible

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None,
                           sticky=True, alert_id="", priority=3, alert_type="watchdog",
                           metadata=None):
                self.alerts.append({
                    "text": str(text), "sticky": bool(sticky),
                    "duration_ms": int(duration_ms), "alert_id": str(alert_id),
                    "priority": int(priority), "alert_type": str(alert_type),
                })

            def resolve_alert(self, alert_id):
                self.resolved.append(str(alert_id))

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self.shown.append(str(text))

        cfg = Config(base=tmp_path)
        # model_access 门开 1.0（本类主链路）；exec_failed 也开 1.0——有两个用例
        # 用模型访问提醒去抑制/不抑制通用失败横幅，exec_failed 关着就测不到抑制分支。
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(model_access=1.0, exec_failed=1.0)
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_first_model_access_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-1"})
        assert mgr.win.alerts, "应弹出模型访问失败提醒"
        alert = mgr.win.alerts[-1]
        assert alert["alert_id"] == "model-access:sess-1", "alert_id 必须带 sessionId 隔离"
        # 可见文案走 legacy model_access.one 预设（模型访问失败语义）；服务端限流码由 alert_id/alert_type 承载
        assert "模型访问失败" in alert["text"]
        assert alert["priority"] == mgr._MODEL_ACCESS_PRIORITY and alert["priority"] == 1

    def test_consecutive_model_access_merged_same_session(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-2"})
        mgr._on_model_access("dsh", {"sessionId": "sess-2"})  # 8s 冷却窗口内 → 合并
        assert mgr._model_access_cache["sess-2"]["count"] == 2
        # 合并后的可见文案走 legacy model_access.many 预设首句：
        # 「当前会话 … 的模型访问已连续失败 {count} 次，请稍后再试。」
        merged = mgr.win.alerts[-1]["text"]
        assert "模型访问" in merged and "连续失败" in merged, merged
        assert "2 次" in merged, merged
        assert mgr.win.alerts[-2]["alert_id"] == mgr.win.alerts[-1]["alert_id"], "同 session 复用同一 alert_id"

    def test_multi_session_isolated(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-A"})
        mgr._on_model_access("dsh", {"sessionId": "sess-B"})
        ids = [a["alert_id"] for a in mgr.win.alerts]
        assert ids == ["model-access:sess-A", "model-access:sess-B"], "不同 session 不得互相顶替"
        assert set(mgr._model_access_cache) == {"sess-A", "sess-B"}

    def test_dismiss_clears_cache_and_alert(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-3"})
        mgr._dismiss_model_access_alert("sess-3")
        assert "sess-3" not in mgr._model_access_cache, "关闭后清理缓存"
        assert "model-access:sess-3" in mgr.win.resolved, "关闭对应 alert"

    def test_execution_failed_suppressed_while_model_access_active(self, tmp_path):
        """存在活跃模型访问失败提醒时，仅真正的模型访问失败（errorCode 属限流类码）不再弹通用横幅；
        模型重试耗尽（retryExhausted）是另一条语义，照常提醒。"""
        mgr = self._make_mgr(tmp_path)
        # 先触发模型访问失败提醒，冷却窗口内再出现真·模型访问失败（errorCode=RATE_LIMIT）→ 抑制
        mgr._on_model_access("dsh", {"sessionId": "sess-4"})
        before = len(mgr.win.alerts)
        mgr._on_execution_failed("dsh", {"sessionId": "sess-4", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True, "errorCode": "RATE_LIMIT"})
        assert len(mgr.win.alerts) == before, "活跃模型访问失败提醒 + 真模型访问失败 → 抑制通用失败横幅"

    def test_retry_exhausted_not_suppressed_as_model_access(self, tmp_path):
        """模型重试耗尽失败（无限流类 errorCode）不是模型访问失败：提醒活跃也不抑制，照常弹 failure.retry。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-5"})
        before = len(mgr.win.alerts)
        mgr._on_execution_failed("dsh", {"sessionId": "sess-5", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True})
        assert len(mgr.win.alerts) == before + 1, "重试耗尽失败不应被当作模型访问失败抑制"
        assert "重试" in mgr.win.alerts[-1]["text"]  # failure.retry 文案

    def test_execution_failed_suppressed_beyond_cooldown_while_429_alive(self, tmp_path):
        """F2 回归：模型访问失败提醒展示 15s > 合并冷却 8s，turn/end 的 execution/failed 常
        在 8~15s 窗口到达——只要提醒仍未 dismiss，通用失败横幅必须继续抑制
        （旧实现按 8s cooldown 判断会绕过抑制造成双弹）。dismiss 后新失败正常提醒。"""
        mgr = self._make_mgr(tmp_path)
        now = [1000.0]
        mgr._clock = lambda: now[0]
        mgr._on_model_access("dsh", {"sessionId": "sess-f2",
                                     "errorCode": "RATE_LIMIT", "consecutiveRetryCount": 1})
        assert len(mgr.win.alerts) == 1
        now[0] += 10.0  # 超出 8s cooldown，仍在 15s 展示寿命内
        mgr._on_execution_failed("dsh", {"sessionId": "sess-f2", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True, "retries": 5,
                                         "errorCode": "RATE_LIMIT"})
        assert len(mgr.win.alerts) == 1, "模型访问失败提醒存活期间不得二次弹通用失败横幅"
        # 收起提醒后：新的（非限流）失败应正常提醒
        mgr._dismiss_model_access_alert("sess-f2")
        mgr._on_execution_failed("dsh", {"sessionId": "sess-f2", "failureType": "tool_failed",
                                         "retryExhausted": False, "retries": 0,
                                         "errorCode": ""})
        assert len(mgr.win.alerts) == 2, "提醒已收起后工具失败应正常提醒"


class TestModelAccessStreakCleanup:
    """F13：清理模型访问提醒时必须同步清空 tracker 内部 streak。

    只清外部镜像（``_model_access_cache`` / ``_model_access_retry_counts``）会
    让 tracker 里按 (source, session) 留存的连续计数残留；重新开启联动后同一
    会话的新一轮失败直接接着旧计数，提醒里出现「已连续 N 次」虚高。
    """

    class _Win:
        def __init__(self):
            self.alerts = []
            self.resolved = []

        def isVisible(self):
            return True

        def show_alert(self, text, **_kwargs):
            self.alerts.append(str(text))

        def resolve_alert(self, alert_id):
            self.resolved.append(str(alert_id))

        def show_bubble(self, *_args, **_kwargs):
            pass

    def _make_mgr(self, tmp_path):
        return AgentLinkManager(self._Win(), Config(base=tmp_path))

    @staticmethod
    def _retry():
        from pet.agent_event_normalizer import normalize_event
        return normalize_event({"event": "llm/retry", "agent": "dsh", "sessionId": "s-1",
                                "errorCode": "RATE_LIMIT", "errorMessage": "429 too many requests"})

    def _feed_and_next_streak(self, mgr):
        """喂一条限流重试，返回 tracker 记账后的连续计数（经 consume 观察）。"""
        out = mgr._model_access_tracker.consume(self._retry())
        assert out is not None, "限流重试必须产出 streak"
        return int(out["consecutiveRetryCount"])

    def test_clear_resets_tracker_streak(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_normalized_event(self._retry())
        mgr._on_normalized_event(self._retry())
        assert self._feed_and_next_streak(mgr) == 3, "前置：tracker 已累计到 3"
        mgr._clear_model_access_alerts()
        assert self._feed_and_next_streak(mgr) == 1, "清理后 tracker streak 必须清零"

    def test_disable_and_reenable_does_not_inherit_old_streak(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_normalized_event(self._retry())
        mgr._on_normalized_event(self._retry())
        # 关闭 DSH 联动：apply_config 走 _clear_model_access_alerts
        cfg = dict(mgr.cfg.get("agent_link", {}))
        cfg["dsh"] = False
        mgr.cfg.set("agent_link", cfg)
        mgr.apply_config()
        # 重新开启后再来一次失败：计数必须从 1 开始（不继承旧 streak）
        mgr._on_normalized_event(self._retry())
        assert self._feed_and_next_streak(mgr) == 2, \
            "重启用后不得继承旧 streak，否则提醒计数虚高"

    def test_normalized_event_signal_reaches_consumer(self, tmp_path):
        """守卫：normalized_event 信号必须直连 _on_normalized_event。

        移除 AgentEventRuntime 分发层后这是该信号的唯一消费接线；其余用例
        全部直调 handler，connect 丢失时它们照样全绿——信号级守卫不可省。
        """
        mgr = self._make_mgr(tmp_path)
        mgr.monitors["dsh"].normalized_event.emit(self._retry())
        assert self._feed_and_next_streak(mgr) == 2, "经信号发射的重试事件必须被消费记账"


class TestSessionNameTruthfulness:
    """{sessionName} 只注入真实会话显示名，绝不把 sessionId 截短占位冒充（字段真实性）。

    会话元数据（session/meta）未到达时，get_session_display_name() 会回退成
    "DSH · <id8>" 兜底占位——它不是「会话显示名」，不得注入台词模板（渲染端
    对缺失的条件字段会自动隐藏 {sessionName} 占位符）。
    """

    class _Win:
        cats = {"acts": ["写代码"]}
        idles = ["待机呼吸"]

        def isVisible(self):
            return True

        def show_bubble(self, *_args, **_kwargs):
            pass

    class _AlertWin:
        def __init__(self):
            self.alerts = []
            self.resolved = []
            # 有意不带 _bubble_busy_until：_schedule_model_access_dismiss 据此跳过 QTimer

        def isVisible(self):
            return True

        def show_alert(self, text, **_kwargs):
            self.alerts.append(str(text))

        def resolve_alert(self, alert_id):
            self.resolved.append(str(alert_id))

        def show_bubble(self, *_args, **_kwargs):
            pass

    def _make(self, tmp_path, win=None):
        cfg = Config(base=tmp_path)
        # 本类取证 model_access 提醒的字段真实性：门开 1.0，避免门关着时
        # 用「什么都没弹」冒充「字段没被注入」。
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(model_access=1.0)
        cfg.set("agent_link", ag)
        return AgentLinkManager(win or self._Win(), cfg)

    def test_id_fallback_is_not_injected_as_session_name(self, tmp_path):
        mgr = self._make(tmp_path)
        sid = "session-0123456789"
        assert mgr.get_session_display_name(sid) == f"DSH · {sid[:8]}"
        cond = mgr._session_conditional({"sessionId": sid})
        assert "sessionName" not in cond, "无元数据时不得把 id 截短占位注入为会话名"
        assert not any("session-" in str(v) for v in cond.values())

    def test_real_session_name_from_meta_is_injected(self, tmp_path):
        mgr = self._make(tmp_path)
        sid = "session-0123456789"
        mgr._on_session_meta("dsh", {"sessionId": sid, "projectName": "深海项目", "sessionName": "排障对话"})
        cond = mgr._session_conditional({"sessionId": sid})
        # sessionName 只取会话名自身，绝不拼 projectName（两字段语义独立）
        assert cond["sessionName"] == "排障对话"
        assert cond["projectName"] == "深海项目", "projectName 是独立字段"
        assert mgr._session_name_or_empty(sid) == "排障对话"
        # 组合展示串仍只属于展示 API（气泡前缀/探索气泡），不冒充会话名字段
        assert mgr.get_session_display_name(sid) == "深海项目 · 排障对话"

    def test_model_access_alert_does_not_inject_session_name_without_meta(self, tmp_path):
        mgr = self._make(tmp_path, win=self._AlertWin())
        captured = {}
        mgr._dialogue = lambda key, fallback, **kw: (captured.update(kw), fallback)[1]
        mgr._show_model_access_alert("session-abcdef12", 1)
        assert "sessionName" not in captured, "模型访问失败提醒无会话元数据时不得注入 sessionName"


class TestInstallFinishedGuard:
    """安装后台线程完成回调不得越过 manager 生命周期：窗口关闭/角色切换
    （shutdown）或重新禁用后，迟到的 install_finished 不得写配置/启动
    监视器/弹气泡（daemon 安装线程本身无法被取消，只能拦完成回调）。"""

    def _make_manager(self, tmp_path, bubbles, monkeypatch, release):
        cfg = Config(base=tmp_path)
        # 本类取证安装代次守卫：bridge 类概率门开 1.0，避免文件头 autouse 基线
        # （全 0.0）把「门抽稀掉的气泡」冒充「守卫正确丢弃了迟到回调」。
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(bridge=1.0)
        cfg.set("agent_link", ag)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            DshMonitor, "uninstall_bridge", classmethod(lambda cls: True),
        )

        def fake_install():
            assert release.wait(timeout=5.0), "测试释放信号未到达"
            return (True, "ok", False)

        monkeypatch.setattr(
            DshMonitor, "install_bridge", classmethod(lambda cls: fake_install()),
        )
        return cfg, mgr

    def _install_thread(self):
        return next(
            t for t in threading.enumerate() if t.name == "dsh-bridge-install"
        )

    def test_late_completion_after_shutdown_is_dropped(self, tmp_path, monkeypatch):
        """安装完成发生在 shutdown（窗口关闭/角色切换）之后 → 完成回调被
        丢弃：配置不写回、监视器不启动、无完成气泡。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        assert "dsh" in mgr._install_pending
        assert bubbles == ["正在为 DSH 安装通信桥。"]  # persona legacy: bridge.install.pending
        mgr.shutdown()   # 安装完成前 manager 被关闭（窗口 close / 角色切换）
        release.set()    # 安装此刻才完成
        install_thread.join(timeout=5.0)
        app.processEvents()
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_queued_completion_dispatched_after_shutdown_is_dropped(self, tmp_path, monkeypatch):
        """emit→dispatch 竞态：信号在 shutdown 前已 emit 入队（worker 已
        完成），但回调在 shutdown 之后才被派发 → 同样必须丢弃（完成回调
        在 GUI 线程的权威校验兜住该窗口）。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        release.set()                 # 安装完成 → worker emit（queued 入队）
        install_thread.join(timeout=5.0)
        # 未跑 processEvents：queued 回调仍躺在 GUI 事件队列里
        mgr.shutdown()                # 关闭发生在回调派发之前
        app.processEvents()           # 迟到的 queued 回调此刻才派发 → 丢弃
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_late_completion_after_redisable_is_dropped(self, tmp_path, monkeypatch):
        """用户重新关闭联动（安装进行中）→ 在途安装作废，完成回调被丢弃，
        不得反向把配置写回 True / 启动监视器。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        assert "dsh" in mgr._install_pending
        mgr.set_enabled("dsh", False)  # 用户重新关闭联动 → 在途安装作废
        assert "dsh" not in mgr._install_pending
        release.set()
        install_thread.join(timeout=5.0)
        app.processEvents()
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_stale_queued_completion_must_not_consume_reinstalled_pending(self, tmp_path, monkeypatch):
        """B9 复审 P1：disable→re-enable 两代安装交错。安装 A 完成信号已
        queued 入队但未派发时，用户关闭联动并再次开启、登记安装 B（新代次）；
        A 的旧回调随后派发时不得消费 B 的 pending（不得写配置/启动监视器/
        弹完成气泡），B 的真实回调随后正常生效。完成信号必须携带并校验
        安装代次，仅凭 agent key 无法区分两代安装。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        cfg = Config(base=tmp_path)
        # 同上：bridge 类概率门开 1.0，保证气泡断言取证的是守卫而非概率门。
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(bridge=1.0)
        cfg.set("agent_link", ag)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            DshMonitor, "uninstall_bridge", classmethod(lambda cls: True),
        )

        release_a, release_b = threading.Event(), threading.Event()
        queue = [release_a, release_b]

        def fake_install():
            ev = queue.pop(0)
            assert ev.wait(timeout=5.0), "测试释放信号未到达"
            return (True, "ok", False)

        monkeypatch.setattr(
            DshMonitor, "install_bridge", classmethod(lambda cls: fake_install()),
        )

        def install_thread():
            return next(
                t for t in threading.enumerate() if t.name == "dsh-bridge-install"
            )

        # 第一代安装 A：等它完成并 emit（queued 入队），但先不派发
        mgr.set_enabled("dsh", True)
        thread_a = install_thread()
        release_a.set()
        thread_a.join(timeout=5.0)
        assert not thread_a.is_alive()
        assert "dsh" in mgr._install_pending   # A 的 pending 尚未被消费
        # 未跑 processEvents：A 的 queued 回调仍躺在 GUI 事件队列里
        mgr.set_enabled("dsh", False)          # 关闭联动：作废在途安装
        assert "dsh" not in mgr._install_pending
        mgr.set_enabled("dsh", True)           # 再次开启：登记安装 B（新代次）
        assert "dsh" in mgr._install_pending
        thread_b = install_thread()
        # 此刻派发 A 的旧 queued 回调：不得消费 B 的 pending
        app.processEvents()
        app.processEvents()
        assert "dsh" in mgr._install_pending           # B 的 pending 必须仍在
        assert cfg.data["agent_link"]["dsh"] is False  # 配置不得被 A 写回
        assert not mgr.monitors["dsh"]._running        # 监视器不得被 A 启动
        assert not any("安装完成" in b for b in bubbles), f"不得弹完成气泡: {bubbles}"
        # B 的真实回调随后派发：正常生效（写配置、启动监视器、弹气泡）
        release_b.set()
        thread_b.join(timeout=5.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            if cfg.data["agent_link"]["dsh"]:
                break
            time.sleep(0.01)
        assert cfg.data["agent_link"]["dsh"] is True
        assert mgr.monitors["dsh"]._running
        assert any("安装完成" in b for b in bubbles), f"应有 B 的完成气泡: {bubbles}"

    def test_normal_completion_still_applies(self, tmp_path, monkeypatch):
        """正常路径回归：安装完成后回调照常生效（写配置、启动监视器、
        弹完成气泡）。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        release.set()
        install_thread.join(timeout=5.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            if cfg.data["agent_link"]["dsh"]:
                break
            time.sleep(0.01)
        assert cfg.data["agent_link"]["dsh"] is True
        assert mgr.monitors["dsh"]._running
        assert any("安装完成" in b for b in bubbles), f"应有完成气泡，实际: {bubbles}"
        mgr.set_enabled("dsh", False)
        assert cfg.data["agent_link"]["dsh"] is False

class TestDetectorAlertThrottle:
    """N2 跨检测器弹窗节流：stuck/pattern/watchdog 同 agent 30s 内只弹一次窗
    （动画照常），升级档位放行，不同 scope 互不影响。"""

    def _make_mgr(self, tmp_path, **gate_overrides):
        class FakeWin:
            def __init__(self):
                self.alerts = []
                self.anims = []
                self._visible = True
                self._bubble_suppressed = False

            def isVisible(self):
                return self._visible

            def request_link_anim(self, anim):
                self.anims.append(str(anim))

            def show_alert(self, text, *, duration_ms=0, sticky=True, **kw):
                # 与 window_alerts.show_alert 一致：设置窗打开期间普通提醒被丢弃。
                if self._bubble_suppressed:
                    return
                self.alerts.append({"text": str(text), "sticky": bool(sticky)})

            def show_bubble(self, text, duration_ms=3000):
                self.alerts.append({"text": str(text), "bubble": True})

        cfg = Config(base=tmp_path)
        # 本类取证 N2 跨检测器节流：stuck/pattern/watchdog 三条检测类概率门开 1.0，
        # 避免文件头 autouse 基线（全 0.0）把「没弹窗」冒充「被节流」。
        gates = {"stuck": 1.0, "pattern": 1.0, "watchdog": 1.0}
        gates.update(gate_overrides)
        ag = dict(cfg.get("agent_link", {}))
        ag["report_gates"] = _agent_gates(**gates)
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(FakeWin(), cfg)
        mgr._clock = lambda: mgr._throttle_now[0]
        mgr._throttle_now = [1000.0]
        return mgr

    def test_watchdog_suppressed_after_stuck_within_window(self, tmp_path):
        """stuck(severity2) 弹窗后 30s 内 watchdog warning 到达 → 弹窗被抑制。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 10.0
        mgr._on_exploration_warning("sess-1", {"agent_key": "dsh", "reasons": ["search"], "steps": []})
        assert len(mgr.win.alerts) == 1, "30s 窗口内 watchdog 弹窗应被抑制"

    def test_watchdog_allowed_after_cooldown_expired(self, tmp_path):
        """超过 30s 后同 agent 再触发 watchdog → 正常弹窗。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 31.0
        mgr._on_exploration_warning("sess-2", {"agent_key": "dsh", "reasons": ["search"], "steps": []})
        assert len(mgr.win.alerts) == 2, "冷却结束后 watchdog 应正常弹窗"

    def test_escalation_pattern_control_overrides_previous(self, tmp_path):
        """watchdog 弹窗后 pattern control（升级/控制级）到达 → 放行（覆盖低档）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_exploration_warning("sess-3", {"agent_key": "dsh", "reasons": ["search"], "steps": []})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 5.0
        mgr._on_pattern_control("dsh", {"verdict": "REPLAN", "reason": "loop", "class": "search", "count": 8, "window": "10"})
        assert len(mgr.win.alerts) == 2, "升级到 control 应放行（覆盖低档提醒）"

    def test_different_scope_not_throttled(self, tmp_path):
        """不同 agent scope 互不影响：agent B 弹窗不受 agent A 的节流记录约束。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_stuck_intervention("agent-a", {"severity": 2})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 5.0
        mgr._on_exploration_warning("sess-b", {"agent_key": "agent-b", "reasons": ["search"], "steps": []})
        assert len(mgr.win.alerts) == 2, "不同 agent scope 不受节流影响"

    def test_pattern_warning_no_alert_but_records_gate(self, tmp_path):
        """pattern warning 只播动画不弹窗、也不该占用节流槽（非弹窗事件）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_pattern_warning("dsh", {})
        assert mgr.win.alerts == []
        # 随后 stuck severity2（升级）不受影响
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1

    def test_same_tier_stuck_escalation_is_throttled(self, tmp_path):
        """N2-b：同级重复升级（stuck 档位 2 → 档位 2）应被节流，不再连环换弹。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 5.0
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1, "同档重复升级应被节流"

    def test_pattern_control_then_stuck_same_tier_is_throttled(self, tmp_path):
        """N2-b：pattern control 已弹窗后，同档 stuck 档位 2 应被节流（对称）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_pattern_control("dsh", {"verdict": "REPLAN", "reason": "loop",
                                        "class": "search", "count": 8, "window": "10"})
        assert len(mgr.win.alerts) == 1
        mgr._throttle_now[0] += 5.0
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1, "同档提醒应被节流"

    def test_suppressed_alert_does_not_consume_throttle_slot(self, tmp_path):
        """N2-a：设置窗打开期间提醒被 show_alert 丢弃，不得占用 30s 节流槽。"""
        mgr = self._make_mgr(tmp_path)
        mgr.win._bubble_suppressed = True
        mgr._on_exploration_warning("sess-1", {"agent_key": "dsh", "reasons": ["search"], "steps": []})
        assert mgr.win.alerts == [], "设置窗打开期间普通提醒应被丢弃"
        mgr.win._bubble_suppressed = False
        mgr._throttle_now[0] += 1.0
        mgr._on_exploration_warning("sess-1", {"agent_key": "dsh", "reasons": ["search"], "steps": []})
        assert len(mgr.win.alerts) == 1, "被丢弃的提醒不该占用节流槽"

    def test_stuck_gate_rejected_alert_does_not_consume_throttle_slot(self, tmp_path):
        """F14：概率门丢弃的提醒不该占 30s 节流槽（stuck 路径）。

        先被概率门拒绝（未展示），随后一条放行的同 scope 提醒必须能正常弹；
        旧实现先记节流槽再判概率门，第二次会被 30s 窗口误压。
        """
        mgr = self._make_mgr(tmp_path, stuck=0.5)
        rolls = iter([0.99, 0.0])
        mgr._rng = lambda: next(rolls)
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert mgr.win.alerts == [], "概率门拒绝时不得弹窗"
        mgr._throttle_now[0] += 5.0
        mgr._on_stuck_intervention("dsh", {"severity": 2})
        assert len(mgr.win.alerts) == 1, "被概率门丢弃的提醒不得占用节流槽"

    def test_pattern_gate_rejected_alert_does_not_consume_throttle_slot(self, tmp_path):
        """F14：pattern.control 同走 stuck 门，被门丢弃的提醒不得占节流槽。"""
        mgr = self._make_mgr(tmp_path, stuck=0.5)
        rolls = iter([0.99, 0.0])
        mgr._rng = lambda: next(rolls)
        payload = {"verdict": "REPLAN", "reason": "loop", "class": "search",
                   "count": 8, "window": "10"}
        mgr._on_pattern_control("dsh", payload)
        assert mgr.win.alerts == [], "概率门拒绝时不得弹窗"
        mgr._throttle_now[0] += 5.0
        mgr._on_pattern_control("dsh", payload)
        assert len(mgr.win.alerts) == 1, "被概率门丢弃的提醒不得占用节流槽"

    def test_watchdog_gate_rejected_alert_does_not_consume_throttle_slot(self, tmp_path):
        """F14：watchdog.warning 同走 stuck 门，被门丢弃的提醒不得占节流槽。"""
        mgr = self._make_mgr(tmp_path, stuck=0.5)
        rolls = iter([0.99, 0.0])
        mgr._rng = lambda: next(rolls)
        payload = {"agent_key": "dsh", "reasons": ["search"], "steps": []}
        mgr._on_exploration_warning("sess-1", payload)
        assert mgr.win.alerts == [], "概率门拒绝时不得弹窗"
        mgr._throttle_now[0] += 5.0
        mgr._on_exploration_warning("sess-1", payload)
        assert len(mgr.win.alerts) == 1, "被概率门丢弃的提醒不得占用节流槽"


# ============================================================================
class TestUnknownBridgeEventReminder:
    """未知桥接事件 → 提醒用户更新/重装 bridge。

    识别：DSH 监视器里，事件名在「语义层 / 状态机 / _poll 直通名单」全部
    不认识才算未知（claude/cursor 的 transcript 噪声不算）。提醒受 bridge
    概率门控制，同一 agent 在冷却窗口内只弹一次（未知事件成串时不刷屏）。
    """

    def _make_mgr(self, tmp_path, gates=None):
        app = QApplication.instance() or QApplication([])
        bubbles = []

        class DummyWin:
            def isVisible(self):
                return True

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        cfg = Config(base=tmp_path)
        if gates is not None:
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), "report_gates": _agent_gates(**gates)}
            cfg.save()
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, bubbles, clock

    def test_monitor_emits_only_unknown_dsh_events(self, tmp_path):
        """监视器只对「全识别路径都不认识」的 DSH 事件发 unknown_bridge_event。"""
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "brand/sparkle", "ts": 1}) + "\n")             # 未知
            f.write(json.dumps({"event": "execution/failed", "ts": 2}) + "\n")          # 语义层已知
            f.write(json.dumps({"event": "agent/status", "state": "working", "ts": 3}) + "\n")  # 状态机已知
            f.write(json.dumps({"event": "model_access", "errorCode": "429", "ts": 4}) + "\n")   # 直通名单已知
            f.write(json.dumps({"event": "cordis/request-run", "ts": 5}) + "\n")        # 直通名单已知
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_user_message_is_not_unknown(self, tmp_path):
        """桥的核心合法事件绝不判为「未知桥接事件」。

        回归：DSH 的 user/message 是扁平记录（无 type/source 字段），语义层
        normalize_event 返回 None、状态机不建模 → 漏登记直通名单会把每次真人
        消息（对话开始）误判成「更新/重装 bridge」提醒（10 分钟冷却 → 表现为
        「有时候触发」的未知事件）。bridge/diagnostic、command/done、
        pet/control-clicked、bridge/control-received 同属漏网：都是桥合法发出
        的事件，语义层未建模，必须经直通名单兜底。真正未知的事件照常触发。
        """
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            # 与桥写出的形态一致：扁平记录，无 type 字段
            f.write(json.dumps({"event": "user/message", "text": "hi", "step": None,
                                "sessionId": "s1", "ts": 1}) + "\n")
            f.write(json.dumps({"event": "bridge/diagnostic", "bridgeDir": "X", "ts": 2}) + "\n")
            f.write(json.dumps({"event": "command/done", "step": 1, "ts": 3}) + "\n")
            f.write(json.dumps({"event": "pet/control-clicked", "ts": 4}) + "\n")
            f.write(json.dumps({"event": "bridge/control-received", "ts": 5}) + "\n")
            f.write(json.dumps({"event": "brand/sparkle", "ts": 6}) + "\n")  # 真未知仍要报
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_watchdog_and_control_events_are_not_unknown(self, tmp_path):
        """桥接/桌宠回显真实会写、语义层与状态机都没建模的事件不得判成「未知」。

        漏登记后果与 user/message 同型：每次写盘触发一次「更新/重装 bridge」
        误提醒（10 分钟冷却 → 表现为偶发弹窗）。来源：
        - tool-workflow/run-end：桥接 STATE_EVENT_TYPES（与已登记的 run-start 成对）；
        - web_search_begin / web_search_end / context_compacted：桥接
          WATCHDOG_EVENT_TYPES 直写（供探索看门狗，非状态迁移）；
        - pet/control-queued：桌宠控制队列写盘回显（pet/dsh_control.py）。
        """
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "tool-workflow/run-end", "step": 1, "ts": 1}) + "\n")
            f.write(json.dumps({"event": "web_search_begin", "ts": 2}) + "\n")
            f.write(json.dumps({"event": "web_search_end", "ts": 3}) + "\n")
            f.write(json.dumps({"event": "context_compacted", "ts": 4}) + "\n")
            f.write(json.dumps({"event": "pet/control-queued", "ts": 5}) + "\n")
            f.write(json.dumps({"event": "brand/sparkle", "ts": 6}) + "\n")  # 真未知仍要报
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_non_dsh_monitor_never_emits_unknown(self, tmp_path):
        """claude/cursor 等 transcript 噪声不算桥接未知事件（只查 DSH 监视器）。"""
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("cursor", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "brand/sparkle", "ts": 1}) + "\n")
        mon._poll()
        assert unknown == []
        mon.stop()

    def test_wiring_manager_bubbles_reminder(self, tmp_path):
        """Monitor → Manager 全链路：bridge 门开时收到未知事件即弹更新/重装提醒。"""
        mgr, bubbles, _ = self._make_mgr(tmp_path, gates={"bridge": 1.0})
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 1
        assert "更新" in bubbles[0] or "重装" in bubbles[0], bubbles[0]
        mgr.shutdown()

    def test_gate_closed_keeps_quiet(self, tmp_path):
        """bridge 门为 0 时未知事件提醒静音（可被用户统一关掉）。"""
        mgr, bubbles, _ = self._make_mgr(tmp_path, gates={"bridge": 0.0})
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert bubbles == []
        mgr.shutdown()

    def test_cooldown_reminds_once_per_window(self, tmp_path):
        """同一 agent 冷却窗口内只提醒一次；窗口过后再次提醒。"""
        mgr, bubbles, clock = self._make_mgr(tmp_path, gates={"bridge": 1.0})
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 1, "冷却窗口内重复未知事件不得刷屏"
        clock[0] += 601.0
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 2, "冷却窗口过后应再次提醒"
        mgr.shutdown()


class TestNotifyDshState:
    """dsh_state 收敛状态注入（notify_dsh_state）回归。

    legacy AgentStatus 基线只有 working/idle（bridge 设计），thinking 等状态由
    dsh_state.py 收敛后经此喂给既有呈现管线——DSH 的思考气泡/对话开始反应
    因此稳定触发（此前该状态对 legacy 监视器结构性不可见）。
    """

    def _make_mgr(self, tmp_path):
        app = QApplication.instance() or QApplication([])
        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        win = DummyWin()
        win.switched = switched
        cfg = Config(base=tmp_path)
        data = cfg.data
        data["agent_link"] = {**data.get("agent_link", {}),
                              "report_gates": _agent_gates(state=1.0)}
        cfg.save()
        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_thinking_drives_bubble_and_anim_when_linked(self, tmp_path):
        """联动开启（白盒模拟 DSH 监视器运行）时，thinking 必须到达气泡+动画。

        此前 DSH 的 thinking 只存在于 dsh_state 收敛结果里、永远不进 legacy 管线，
        思考气泡从不触发；notify_dsh_state 必须打通这条链。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr.monitors["dsh"]._running = True  # white-box：等效 agent_link.dsh 已启用
        try:
            mgr.notify_dsh_state("thinking")
        finally:
            mgr.shutdown()
        assert any("思考" in b for b in bubbles), bubbles
        assert win.switched, "thinking 必须驱动联动动画"

    def test_thinking_noop_when_link_disabled(self, tmp_path):
        """DSH 联动未开启（监视器未运行）时注入为 no-op，不惊动用户。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        try:
            mgr.notify_dsh_state("thinking")
        finally:
            mgr.shutdown()
        assert bubbles == []
        assert win.switched == []
