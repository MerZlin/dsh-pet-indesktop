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
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import pet.agent_link as agent_link
from pet.agent_link import (
    AgentLinkManager,
    BaseAgentMonitor,
    ByteOffsetTailer,
    ClaudeCodeMonitor,
    CursorMonitor,
    CustomAgentMonitor,
    DshMonitor,
    normalize_event_state,
)
from pet.config import Config
from pet.config import _clean_agent_link_data, _clean_custom_agents


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


# ============================================================================
# 2. 状态映射与规范化测试
# ============================================================================
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
        cfg = Config(base=tmp_path)
        assert cfg.data["agent_link"] == {
            "dsh": False,
            "claude": False,
            "cursor": False,
            "opencode": False,
            "custom_agents": [],
            "notify_state": False,
            "notify_done": True,
            "notify_activity": False,
            "notify_exec_failed": True,
            "stuck_detect": False,
            "stuck_worried_threshold": 3,
            "stuck_intervene_threshold": 5,
            "stuck_window_seconds": 90,
            "stuck_cooldown_seconds": 300,
            "stuck_reminder_text": "",
            "exploration_watchdog_enabled": True,
            "exploration_watchdog_mode": "manual",
            "exploration_watchdog_warning_threshold": 3,
            "exploration_watchdog_control_threshold": 5,
            "exploration_watchdog_judge_model": "",
            "exploration_watchdog_judge_timeout": 8,
            "exploration_watchdog_cooldown_steps": 3,
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
        win = DummyPetWindow()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)  # 测试关闭节流，逐个验证状态映射

        # 模拟 Agent 状态分发（busy 动作池轮换：写代码→吃Token）
        mgr._on_agent_state("claude", "thinking")
        assert "写代码" in switched_anims

        mgr._on_agent_state("claude", "working")
        assert "吃Token" in switched_anims

        mgr._on_agent_state("claude", "attention")
        # busy 后的 attention（Claude Stop=回合结束）不再立即弹「看一眼」，
        # 改由完成确认流程接管（防双气泡）；确认后弹中性完成文案
        assert not any("需要你看一眼" in b for b in bubbles)
        assert "claude" in mgr._done_pending
        mgr._fire_done("claude")
        assert any("自己看一眼" in b for b in bubbles)

        # 非 busy 后独立出现的 attention 仍立即提醒
        mgr._on_agent_state("dsh", "attention")
        assert any("需要你看一眼" in b for b in bubbles)


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
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 1.0  # 1s < 2s 节流间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["写代码"]  # 被节流
        clock[0] += 2.0  # 超过间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["写代码", "吃Token"]  # 动作池轮换：写代码→吃Token


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


class TestCooldownUnits:
    def test_seconds_and_minutes_conversion(self, tmp_path):
        """冷却间隔秒/分钟双单位：45 秒应存为 0.75 分钟。"""
        from PySide6.QtWidgets import QApplication
        from pet.settings_dialog import PetSettingsDialog

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        dlg = PetSettingsDialog(cfg)
        if not hasattr(dlg, "pro_cooldown_unit"):
            import pytest
            pytest.skip("非 Windows 无主动识屏设置组")

        # 切到秒，设 45 秒
        dlg.pro_cooldown_unit.setCurrentIndex(1)
        dlg.pro_cooldown_spin.setValue(45)
        assert abs(dlg._cooldown_minutes_value() - 0.75) < 1e-9

        # 切回分钟应自动换算显示
        dlg.pro_cooldown_unit.setCurrentIndex(0)
        assert abs(dlg.pro_cooldown_spin.value() - 0.75) < 1e-9

        # 保存后配置为分钟值
        dlg._save()
        assert abs(cfg.data["proactive_screen"]["cooldown_minutes"] - 0.75) < 1e-9


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


# ============================================================================
# 12. DSH profile 枚举（桥接插件安装/卸载目标）
# ============================================================================
class TestDshProfileEnumeration:
    """_list_profiles 只认含 cordis.yml 的目录，过滤 node_modules 等杂项残留。"""

    def test_filters_non_profile_dirs(self, tmp_path, monkeypatch):
        dsh_home = tmp_path / "dsh-home"
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", dsh_home)
        profiles = dsh_home / "profiles"
        for name in ("web", "headless"):
            d = profiles / name
            d.mkdir(parents=True)
            (d / "cordis.yml").write_text("{}", encoding="utf-8")
        # 包管理器/误操作残留的杂项目录，不应被当作 profile
        (profiles / "node_modules").mkdir()
        (profiles / "empty-dir").mkdir()
        assert DshMonitor._list_profiles() == ["headless", "web"]

    def test_fallback_when_profiles_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path / "dsh-home")
        assert DshMonitor._list_profiles() == ["web"]

    def test_fallback_when_no_valid_profiles(self, tmp_path, monkeypatch):
        dsh_home = tmp_path / "dsh-home"
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", dsh_home)
        (dsh_home / "profiles" / "node_modules").mkdir(parents=True)
        assert DshMonitor._list_profiles() == ["web"]

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
    def _make_mgr(self, tmp_path, agent_link_cfg=None):
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

        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_working_to_idle_done_bubble(self, tmp_path):
        """1. working→idle 后，mgr._done_pending 里出现 'dsh' 的定时器；
        手动调 mgr._fire_done('dsh') 后 fake win 的 show_bubble 收到含「干完活啦」的文本。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        assert "dsh" not in mgr._done_pending

        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        mgr._fire_done("dsh")
        assert any("干完活啦" in b for b in bubbles)

    def test_notify_done_false_no_bubble(self, tmp_path):
        """2. 同样流程但 cfg 里 agent_link.notify_done=False → _fire_done 后无气泡。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, agent_link_cfg={"notify_done": False})
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        mgr._fire_done("dsh")
        assert bubbles == []

    def test_notify_state_start_bubble(self, tmp_path):
        """3. notify_state=True 时，thinking→working 连续两个 busy 状态只弹一次「开始干活啦」
        （第二次 prev_raw 已是 busy 不弹）；默认 notify_state=False 时不弹。"""
        # 默认 notify_state=False
        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)
        mgr_off._on_agent_state("dsh", "thinking")
        assert bubbles_off == []
        clock_off[0] += 3.0
        mgr_off._on_agent_state("dsh", "working")
        assert bubbles_off == []

        # notify_state=True
        mgr_on, win_on, bubbles_on, clock_on = self._make_mgr(tmp_path, agent_link_cfg={"notify_state": True})
        mgr_on._on_agent_state("dsh", "thinking")
        assert len(bubbles_on) == 1
        assert "正在深度思考" in bubbles_on[0]

        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        # 连续 busy 状态，thinking→working 互跳不重复弹
        assert len(bubbles_on) == 1

        # idle 后再 working → 弹「开始干活啦」
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "idle")
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        assert len(bubbles_on) == 2
        assert "开始干活啦" in bubbles_on[1]

    def test_thinking_text_custom_override(self, tmp_path):
        """自定义 thinking 文案：agent_link.thinking_text 非空时优先使用，支持 {name} 占位符。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, agent_link_cfg={"notify_state": True, "thinking_text": "{name} 大脑飞速运转中……"}
        )
        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == 1
        assert "DSH 大脑飞速运转中……" == bubbles[0]
        assert "深度思考" not in bubbles[0]

        # 空字符串 → 回退默认
        mgr2, win2, bubbles2, _ = self._make_mgr(
            tmp_path / "b", agent_link_cfg={"notify_state": True, "thinking_text": ""}
        )
        mgr2._on_agent_state("dsh", "thinking")
        assert "大肥鱼正在深度思考" in bubbles2[0]

    def test_jitter_cancel_done_check(self, tmp_path):
        """4. working→idle→working 抖动：idle 后 pending 存在，
        再来 working 后 pending 被清空（_cancel_done_check 生效），此后 _fire_done 不弹气泡。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
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
        前进超过 5 秒后正常弹。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 1
        assert "干完活啦" in bubbles[0]

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
        assert "干完活啦" in bubbles[1]

    def test_error_during_busy_done_bubble_text(self, tmp_path):
        """6. busy 期间出现 error 再 idle：完成气泡文案含「自己看一眼」而不是「干完活啦」。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "error")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")

        # error 状态本身不立即弹气泡（由完成流程接管，防双气泡）；
        # 完成气泡应当含有「自己看一眼」且不含「干完活啦」
        done_bubbles = [b for b in bubbles if "自己看一眼" in b]
        assert len(done_bubbles) == 1
        assert not any("干完活啦" in b for b in bubbles)

    def test_bubble_busy_until_occupancy(self, tmp_path, monkeypatch):
        """7. _show_link_bubble 在 win._bubble_busy_until 为未来时间时：
        important=False 直接丢弃；important=True 时不立即弹（走 QTimer.singleShot 延后重试，测试里只需断言没有立即调用 show_bubble）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        win._bubble_busy_until = time.time() + 100.0

        # important=False 丢弃
        mgr._show_link_bubble("普通消息", important=False)
        assert bubbles == []

        # important=True 走 singleShot 延后重试，不立即调用 show_bubble
        mgr._show_link_bubble("重要消息", important=True)
        assert bubbles == []

    def test_busy_to_attention_counts_as_done(self, tmp_path):
        """8. Claude 风格：working→attention(Stop) 进入完成确认，不弹立即提醒，
        确认后弹完成气泡（因见过 attention 用中性文案）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "attention")
        assert "claude" in mgr._done_pending  # 进入完成确认
        assert bubbles == []  # 不弹立即 attention 气泡（防双气泡）

        mgr._fire_done("claude")
        assert any("自己看一眼" in b for b in bubbles)
        assert not any("干完活啦" in b for b in bubbles)

    def test_standalone_attention_immediate_bubble(self, tmp_path):
        """9. 非 busy 后独立出现的 attention：立即提醒，不进完成流程。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "attention")
        assert "claude" not in mgr._done_pending
        assert any("看一眼" in b for b in bubbles)

    def test_done_restores_idle_anim_unless_others_busy(self, tmp_path):
        """10. 完成确认后恢复待机动画（Claude 没有 idle 事件，靠这步回待机）；
        另有 Agent 在忙时不恢复（避免顶掉对方的工作动画）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "idle")
        mgr._fire_done("claude")
        assert win.switched[-1] == "待机呼吸"  # 恢复待机
        assert mgr._last_applied["claude"][0] == "idle"

        # 另一 Agent 在忙：不恢复
        mgr2, win2, bubbles2, clock2 = self._make_mgr(tmp_path)
        mgr2._on_agent_state("claude", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("dsh", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("claude", "idle")
        switched_before = len(win2.switched)
        mgr2._fire_done("claude")
        assert len(win2.switched) == switched_before  # 没有切回待机


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
        cfg.data["agent_link"].update({"sound_enabled": True, **sound_cfg})
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

        ok, message = DshMonitor.install_bridge()

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
        """3. 过程汇报：cfg agent_link.notify_activity=True 时 mgr._on_agent_activity('dsh','bash') → 气泡含「正在跑命令」；
        10 秒内第二次任何工具不弹；同工具 60 秒内不重复（clock 前进 15s 再发 bash 仍不弹；换成 read 则弹「正在读文件」）；
        全局限流 8s（另一 agent 在 8s 内也不弹）。notify_activity 默认 False 时不弹。未知工具（如 'frobnicate'）弹安全兜底文案。"""
        # notify_activity 默认 False 时不弹
        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)
        mgr_off._on_agent_activity("dsh", "bash")
        assert bubbles_off == []

        # notify_activity = True
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, agent_link_cfg={"notify_activity": True})

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
        assert "正在跑命令" in bubbles[-1]
        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "memory_search")
        assert "正在翻记忆" in bubbles[-1]

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
            "gemini": True,       # 自定义键的开关布尔（set_enabled 写入路径）
            "notify_done": False,
        })
        assert cleaned["custom_agents"] == [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}]
        assert cleaned["gemini"] is True
        assert cleaned["notify_done"] is False


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

            def _toggle_agent_link(self, key, on, action=None):
                toggles.append((key, on))

            def _set_agent_link_option(self, key, on):
                options.append((key, on))

        menu = QMenu()
        try:
            add_agent_link_menu(menu, DummyPet())
            sub = menu.actions()[0].menu()
            texts = [a.text() for a in sub.actions()]
            # 内置 4 项仍在，自定义项按显示名插入
            for label in ("DeepSeek Harness (DSH)", "Claude Code", "Cursor", "OpenCode"):
                assert label in texts
            assert "Gemini CLI" in texts

            gemini_act = next(a for a in sub.actions() if a.text() == "Gemini CLI")
            gemini_act.setChecked(True)
            assert toggles == [("gemini", True)]
        finally:
            menu.deleteLater()


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
                    self.shown_buttons.append((str(text), [lbl for lbl, _cb in buttons]))

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True, alert_id=""):
                self._alert_queue.append({"id": alert_id, "text": str(text), "sticky": sticky})
                if self._alert_current is None and self._alert_queue:
                    self._alert_current = self._alert_queue.pop(0)
                    self._sticky_bubble_active = self._alert_current.get("sticky", True)
                self.sticky_shown.append((str(text), sticky))
                if buttons:
                    self.shown_buttons.append((str(text), [lbl for lbl, _cb in buttons]))

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
        mgr._on_approval_request("dsh", {"tool": "bash"})
        assert self._agent_keys(mgr) == {"dsh"}
        pending = self._single_pending(mgr, "dsh")
        assert pending["kind"] == "approval"
        assert pending["interactive"] is False
        assert mgr.win._sticky_bubble_active is True
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "审批" in text
        assert mgr.win.shown_buttons == [], "无 rpcId 时不得出按钮（纯提示）"

    def test_approval_request_shows_full_command(self, tmp_path):
        """审批气泡必须展示被审批命令的完整内容（来自 bridge 的 command 字段）。"""
        mgr = self._make_mgr(tmp_path)
        cmd = "pip install pytest --index-url http://mirrors.aliyun.com/pypi/simple/"
        mgr._on_approval_request("dsh", {"tool": "bash", "command": cmd})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert cmd in text, "气泡文案必须包含命令完整内容"
        assert self._single_pending(mgr, "dsh")["command"] == cmd

    def test_approval_request_formats_command_single_line(self, tmp_path):
        """多行/多空格命令折叠成单行展示（气泡图片不保留换行）。"""
        mgr = self._make_mgr(tmp_path)
        raw = "pip install pytest\n\n  --index-url http://example.com/simple/\n"
        mgr._on_approval_request("dsh", {"tool": "bash", "command": raw})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "\n" not in text, "换行必须折叠成空格"
        assert "pip install pytest --index-url http://example.com/simple/" in text

    def test_approval_request_truncates_overlong_command(self, tmp_path):
        """超长命令截断并加省略号，避免撑爆气泡。"""
        mgr = self._make_mgr(tmp_path)
        long_cmd = "x" * 500
        mgr._on_approval_request("dsh", {"tool": "bash", "command": long_cmd})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "…" in text
        assert "x" * 500 not in text

    def test_approval_request_command_missing_falls_back_to_tool(self, tmp_path):
        """无 command 字段时回退到工具名文案（兼容旧桥接路径）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "write"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert "请求执行" not in text
        assert "审批" in text

    def test_approval_resolved_dismisses(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        mgr._on_approval_resolved("dsh")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_concurrent_approvals_resolved_last(self, tmp_path):
        """两个 agent 并发审批：各自 pending；队列模型下逐条关闭并推进下一条。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        mgr._on_approval_request("claude", {"tool": "write"})
        assert self._agent_keys(mgr) == {"dsh", "claude"}
        mgr._on_approval_resolved("dsh")
        assert self._agent_keys(mgr) == {"claude"}
        assert mgr.win._sticky_bubble_active is True, "dsh 审批关闭后 claude 审批顶上，气泡仍挂着"
        mgr._on_approval_resolved("claude")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 2
        assert mgr.win._sticky_bubble_active is False

    def test_idle_dismisses_approval(self, tmp_path):
        """agent 回待机但没收到 decided：交互必然失效，兜底清掉（含窗口隐藏时）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        mgr._on_agent_state("dsh", "idle")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1

    def test_dismiss_all_approvals(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        mgr.dismiss_all_approvals()
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_approval_records_saw_alert(self, tmp_path):
        """审批打断算"需要主人看一眼"：完成后不误说"干完活啦"。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        assert "dsh" in mgr._saw_alert

    def test_resolved_unknown_agent_noop(self, tmp_path):
        """没有对应 pending 的 resolved 事件是空操作，不误关气泡。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_resolved("dsh")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 0

    # ---- 用户问题（ask_user_question）与审批同待遇 ----
    QUESTIONS = [
        {"id": "q1", "question": "要执行哪个方案？",
         "options": [{"label": "方案 A"}, {"label": "方案 B"}, {"label": "方案 C"}],
         "multiSelect": False},
    ]

    def test_question_request_shows_sticky_with_options(self, tmp_path):
        """question/requested 带 options：常驻气泡列出选项，让用户选一个才能继续。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS})
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
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS})
        mgr._on_question_resolved("dsh")
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
        assert "需要你输入" in text
        assert mgr.win.shown_buttons == [], "自由输入问题不出可点按钮"

    def test_question_multi_question(self, tmp_path):
        """一次多个问题：提示有几个问题等你回答。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "a", "question": "Q1"}, {"id": "b", "question": "Q2"}]}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "2 个问题" in text

    def test_question_and_approval_independent(self, tmp_path):
        """并发一个审批 + 一个问题：各自独立 pending，不再互相覆盖；分别 resolved 后全部关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS})
        # 同一 agent 的多个交互各自独立存储（不再互相覆盖）
        assert self._agent_keys(mgr) == {"dsh"}
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 2, "审批和问题应共存，各自一条 pending"
        # 审批和问题各自有 kind
        kinds = {item["kind"] for item in pending.values()}
        assert kinds == {"approval", "question"}
        # 分别 resolved：先关闭问题
        mgr._on_question_resolved("dsh")
        assert len(mgr.pending_interactions_for("dsh")) == 1, "问题关闭后审批还在"
        assert self._single_pending(mgr, "dsh")["kind"] == "approval"
        # 再关闭审批
        mgr._on_approval_resolved("dsh")
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

    def test_hint_upgraded_to_interactive(self, tmp_path):
        """先到无 rpcId 的提示，后到带 rpcId 的同款交互：升级为可点选气泡。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS})
        assert mgr.win.shown_buttons == []
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-3", "sessionId": "s-1"}
        )
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["interactive"] is True
        assert mgr.win.shown_buttons[-1][1] == ["方案 A", "方案 B", "方案 C"]

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

        # 后到无 rpcId 的提示：不降级
        mgr._on_approval_request("dsh", {"tool": "bash"})
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

    def test_build_respond_without_rpcid_is_none(self, tmp_path):
        """无 rpcId 的纯提示交互：没有可回写消息（返回 None）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash"})
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
# 硬失败（execution/failed）：DSH 已决定本轮不再继续，直接提醒
# ============================================================================
class TestExecutionFailed:
    """execution/failed 不经行为分析直接提醒：失败动画 + 气泡。"""

    def _make_mgr(self, tmp_path, notify_exec_failed=True):
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
        ag["notify_exec_failed"] = notify_exec_failed
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_retry_exhausted_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"source": "model_request", "retryExhausted": True, "retries": 4})
        assert mgr.win.alerts, "应入队失败提醒"
        assert "运行失败" in mgr.win.alerts[-1]["text"]
        assert "重试" in mgr.win.alerts[-1]["text"]
        assert mgr.win.alerts[-1]["sticky"] is False, "失败提醒是限时气泡（非 sticky）"

    def test_tool_failure_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"source": "tool", "retryExhausted": False})
        assert mgr.win.alerts
        assert "工具执行最终失败" in mgr.win.alerts[-1]["text"]

    def test_generic_failure(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {})
        assert mgr.win.alerts
        assert "运行失败" in mgr.win.alerts[-1]["text"]

    def test_picks_fail_anim(self, tmp_path):
        """角色动作池含「失败/冒烟」类动作时选择它。"""
        mgr = self._make_mgr(tmp_path)
        mgr.win.cats = {"acts": ["待机", "失败冒烟", "写代码"]}
        anim = mgr._pick_fail_anim()
        assert anim == "失败冒烟"

    def test_notify_exec_failed_disabled(self, tmp_path):
        """notify_exec_failed=False 时不提醒。"""
        mgr = self._make_mgr(tmp_path, notify_exec_failed=False)
        mgr._on_execution_failed("dsh", {"source": "model_request", "retryExhausted": True})
        assert mgr.win.shown == []


# ============================================================================
# 429 限流提醒：独立事件、同 session 合并、多 session 隔离、可关闭
# ============================================================================
class TestRateLimitAlert:
    """rate_limit 事件 → 高优先级提醒，合并计数，按 session 隔离，可关闭。"""

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self.alerts: list[dict] = []
                self.resolved: list[str] = []
                self.shown: list[str] = []
                self._visible = True
                # 有意不提供 _bubble_busy_until：_schedule_429_dismiss 会据 sentinel 跳过 QTimer

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
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_first_429_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_rate_limit("dsh", {"sessionId": "sess-1"})
        assert mgr.win.alerts, "应弹出 429 提醒"
        alert = mgr.win.alerts[-1]
        assert alert["alert_id"] == "429-rate-limit:sess-1", "alert_id 必须带 sessionId 隔离"
        assert "429" in alert["text"] and "限流" in alert["text"]
        assert alert["priority"] == mgr._429_PRIORITY and alert["priority"] == 1

    def test_consecutive_429_merged_same_session(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_rate_limit("dsh", {"sessionId": "sess-2"})
        mgr._on_rate_limit("dsh", {"sessionId": "sess-2"})  # 8s 冷却窗口内 → 合并
        assert mgr._429_cache["sess-2"]["count"] == 2
        assert "已连续限流 2 次" in mgr.win.alerts[-1]["text"]
        assert mgr.win.alerts[-2]["alert_id"] == mgr.win.alerts[-1]["alert_id"], "同 session 复用同一 alert_id"

    def test_multi_session_isolated(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_rate_limit("dsh", {"sessionId": "sess-A"})
        mgr._on_rate_limit("dsh", {"sessionId": "sess-B"})
        ids = [a["alert_id"] for a in mgr.win.alerts]
        assert ids == ["429-rate-limit:sess-A", "429-rate-limit:sess-B"], "不同 session 不得互相顶替"
        assert set(mgr._429_cache) == {"sess-A", "sess-B"}

    def test_dismiss_clears_cache_and_alert(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_rate_limit("dsh", {"sessionId": "sess-3"})
        mgr._dismiss_429_alert("sess-3")
        assert "sess-3" not in mgr._429_cache, "关闭后清理缓存"
        assert "429-rate-limit:sess-3" in mgr.win.resolved, "关闭对应 alert"

    def test_execution_failed_suppressed_while_429_active(self, tmp_path):
        """存在活跃 429 时，不再弹通用失败横幅，避免双重通知。"""
        mgr = self._make_mgr(tmp_path)
        # 先触发 429，冷却窗口内再出现 execution/failed
        mgr._on_rate_limit("dsh", {"sessionId": "sess-4"})
        before = len(mgr.win.alerts)
        mgr._on_execution_failed("dsh", {"sessionId": "sess-4", "source": "model_request",
                                         "retryExhausted": True})
        assert len(mgr.win.alerts) == before, "429 活跃时抑制通用失败横幅"
