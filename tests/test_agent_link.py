# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动单元测试。

测试覆盖：
- 默认全关；
- 有界 Byte-Offset Tailer：新增行增量读取、重复读取不重放、文件轮转/截断安全、backfill 防护；
- 事件 JSONL 解析与状态规范化映射；
- AgentLinkManager 生命周期与 pause / resume；
- 状态变更触发桌宠行为与气泡反馈；
- Claude Code 确认框逻辑（拒绝则不写入 hooks）；
- B9：监视器 I/O 后台线程化（事件/屏障同步，不用 sleep 猜时序）。
"""

from __future__ import annotations

import json
import threading
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
    OpenCodeMonitor,
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

        # B9 同步 seam：直接跑 worker 主体（后台轮询线程执行的同一段代码），
        # 不启动定时器/后台线程，测试完全确定、无时序抖动
        mon._running = True
        mon._poll_worker()  # 初始化 tailer

        # 模拟 Cursor 追加写入事件行
        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PreToolUse"}) + "\n")
            f.write(json.dumps({"type": "Stop"}) + "\n")

        mon._poll_worker()

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
        # B9 同步 seam：直接跑 worker 主体（含常驻只读连接 + 增量查询）
        mon._running = True
        mon._poll_worker()  # 首次 = backfill，不产生事件
        assert received == []

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.updated.1', '{\"info\":{\"role\":\"user\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 3, 'message.part.updated.1', '{\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 4, 'session.updated.1', '{}')")
        db.commit()
        db.close()

        mon._poll_worker()
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
        mon._running = True  # B9 同步 seam
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"tool/call","tool":"bash"}\n')
        mon._poll_worker()
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
        mon._running = True  # B9 同步 seam
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"AgentStatus","state":"working"}\n')
        mon._poll_worker()
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
        # B9 同步 seam：直接跑 worker 主体（子代理过滤保留在读取层）
        mon._running = True
        mon._poll_worker()  # backfill

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('c', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 2, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"tool\",\"tool\":\"bash\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 3, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-finish\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
        assert received == [] and tools == []  # 子代理全程静默

        # 主会话正常报
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('r', 4, 'message.part.updated.1', "
                   "'{\"sessionID\":\"root1\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
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
        # B9 同步 seam
        mon._running = True
        mon._poll_worker()
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"x\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
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
        # B9 同步 seam：直接跑 worker 主体
        mon._running = True
        mon._poll_worker()  # backfill 初始化

        with open(events, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 1.0, "state": "working"}) + "\n")
            f.write(json.dumps({"ts": 2.0, "event": "PreToolUse", "tool": "bash"}) + "\n")
            f.write(json.dumps({"ts": 3.0, "state": "idle"}) + "\n")

        mon._poll_worker()
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
        # B9 同步 seam
        mon._running = True
        mon._poll_worker()
        mon._poll_worker()
        assert states == []

        missing.write_text('{"state": "working"}\n', encoding="utf-8")
        mon._poll_worker()  # 首次发现文件：backfill，不回放历史
        assert states == []

        with open(missing, "a", encoding="utf-8") as f:
            f.write('{"state": "idle"}\n')
        mon._poll_worker()
        assert states == [("gemini", "idle")]
        mon.stop()

    def test_start_does_not_create_dirs(self, tmp_path):
        """只读监听：绝不替用户在任意路径创建目录。"""
        app = QApplication.instance() or QApplication([])
        mon = CustomAgentMonitor(
            "gemini", tmp_path / "cfg", str(tmp_path / "deep" / "nested" / "ev.jsonl"),
        )
        # B9 同步 seam：只读监听不创建目录（不启动定时器/后台线程）
        mon._running = True
        mon._poll_worker()
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
# B9：监视器 I/O 移出 GUI 线程（后台 worker + 跨线程 QueuedConnection）
# ============================================================================
class TestMonitorBackgroundPolling:
    """B9 硬约束验证（全部事件/屏障同步，不用 sleep 猜时序）：

    1. 文件/SQLite I/O 在后台线程执行，GUI 线程只收归一化信号；
    2. 跨线程信号走 QueuedConnection（事件循环不跑则信号不送达）；
    3. stop 后后台线程退出且不再发信号（_emit_* 的 _running 守卫）；
    4. pause/resume 语义不变，worker 全程存活供 resume 立即恢复；
    5. OpenCode 只读连接常驻复用；库文件被删/损坏后优雅降级并自动恢复；
    6. 子代理会话过滤保留在读取层（后台轮询路径同样不产生信号）。
    """

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    @staticmethod
    def _write_event(mon, line: str) -> None:
        with open(mon.events_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def test_io_runs_on_worker_thread_and_signal_is_queued(self, tmp_path, monkeypatch):
        """I/O（read_new_lines）执行线程 ≠ MainThread；worker 完成读取后信号排队，
        不跑事件循环则不送达（跨线程 QueuedConnection），processEvents 后送达。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()

        io_threads = []
        orig_read = ByteOffsetTailer.read_new_lines

        def recording_read(self_, *args, **kwargs):
            io_threads.append(threading.current_thread().name)
            return orig_read(self_, *args, **kwargs)

        monkeypatch.setattr(ByteOffsetTailer, "read_new_lines", recording_read)

        received = []
        mon.state_changed.connect(lambda k, s: received.append((k, s)))
        mon.start()
        try:
            mon._poll()                       # tick1：backfill
            assert mon._worker_done.wait(5)   # 屏障：worker 已完成本轮
            assert io_threads and io_threads[0] != "MainThread"
            assert received == []

            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()                       # tick2：读取新行
            assert mon._worker_done.wait(5)   # 屏障：读取完成
            # 事件循环尚未运行 → 跨线程信号仍排队（QueuedConnection 行为）
            assert received == []
            app.processEvents()               # 事件循环：交付排队信号
            assert received == [("dsh", "working")]
        finally:
            mon.stop()

    def test_stop_suppresses_all_further_signals(self, tmp_path):
        """stop 后后台线程退出；worker 主体即使被直接调用也不再发信号。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s: received.append((k, s)))
        mon.start()
        try:
            mon._poll()                       # tick1：backfill（写文件之前）
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()                       # tick2：读取新行
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == [("dsh", "working")]

            mon.stop()
            assert mon._worker_thread is not None
            mon._worker_thread.join(5)            # 屏障：等后台线程真正退出
            assert not mon._worker_thread.is_alive()

            self._write_event(mon, '{"event": "Stop"}')
            mon._poll_worker()                    # 直接跑 worker 主体
            app.processEvents()
            assert received == [("dsh", "working")]   # 不再新增
        finally:
            mon.stop()

    def test_emit_guard_blocks_after_stop(self, tmp_path):
        """_emit_* 的 _running 守卫：stop（_running=False）后任何发射路径都被拦截。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got = []
        mon.state_changed.connect(lambda k, s: got.append(s))
        mon._running = True
        mon._emit_state("working")
        assert got == ["working"]
        mon._running = False      # 模拟 stop() 之后
        mon._emit_state("idle")
        mon._emit_activity("bash")
        assert got == ["working"]

    def test_pause_halts_scheduling_resume_restores(self, tmp_path):
        """pause/resume 语义不变：pause 停定时器（不再调度轮询），
        resume 恢复；后台 worker 全程存活，resume 立即恢复。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.start()
        try:
            assert mon.is_running() is True
            assert mon._timer.isActive()
            worker = mon._worker_thread
            assert worker is not None and worker.is_alive()

            mon.pause()
            assert mon.is_running() is False
            assert not mon._timer.isActive()
            assert worker.is_alive()          # worker 保留，resume 无需重启

            mon.resume()
            assert mon.is_running() is True
            assert mon._timer.isActive()
            assert mon._worker_thread is worker
        finally:
            mon.stop()

    def test_stop_then_start_restarts_worker(self, tmp_path):
        """stop 后 start 重建后台线程（新代 worker），监控恢复正常。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        try:
            mon._poll()                        # backfill
            assert mon._worker_done.wait(5)
            first_worker = mon._worker_thread
            assert first_worker is not None

            mon.stop()
            first_worker.join(5)               # 屏障：第一代 worker 退出
            assert not first_worker.is_alive()

            mon.start()                        # 重启 → 新代 worker
            assert mon._worker_thread is not first_worker
            mon._poll()                        # backfill（跳过旧行）
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == ["working"]     # 重启后监控恢复正常
        finally:
            mon.stop()

    def test_manager_receives_monitor_signals_via_queued_connection(self, tmp_path):
        """AgentLinkManager 对监视器信号显式 QueuedConnection：
        即使同线程发射也不立即送达，必须跑事件循环。"""
        app = self._app()
        switched = []

        class DummyWin:
            cats = {"acts": ["写代码"]}
            idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                pass

            def _switch(self, name):
                pass

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=0.0)
        mon = mgr.monitors["dsh"]
        # 模拟后台线程产出归一化结果：发射监视器信号
        mon.state_changed.emit("dsh", "working")
        assert switched == []          # 未送达（QueuedConnection 排队中）
        app.processEvents()            # 事件循环 → 送达
        assert switched == ["写代码"]

    def test_opencode_connection_reused_across_polls(self, tmp_path, monkeypatch):
        """OpenCode 只读连接常驻复用：多轮轮询只 connect 一次。"""
        import sqlite3

        app = self._app()
        db_path = tmp_path / "opencode.db"
        real_connect = sqlite3.connect
        db = real_connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.commit()
        db.close()

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        calls = []

        def counting_connect(*a, **kw):
            calls.append(a)
            return real_connect(*a, **kw)

        monkeypatch.setattr(agent_link.sqlite3, "connect", counting_connect)

        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        # B9 同步 seam
        mon._running = True
        mon._poll_worker()   # backfill：首次连接
        assert len(calls) == 1

        db = real_connect(db_path)   # 测试侧写入用原始 connect，不影响计数
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.updated.1', "
                   "'{\"info\":{\"role\":\"user\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
        mon._poll_worker()
        assert len(calls) == 1            # 常驻复用，不再 connect/close
        assert received == ["thinking"]
        mon.stop()

    def test_opencode_db_corruption_and_deletion_recovery(self, tmp_path):
        """库文件损坏/被删：优雅降级（关连接、标记未就绪），
        文件恢复后自动重连 + backfill（不重放历史），增量正常。"""
        import sqlite3

        app = self._app()
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.commit()
        db.close()

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        # B9 同步 seam
        mon._running = True

        mon._poll_worker()   # backfill
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.updated.1', "
                   "'{\"info\":{\"role\":\"user\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
        assert received == ["thinking"]

        # 损坏：先关连接（释放句柄，Windows 上才能覆写/删除），再写垃圾字节
        mon._close_db()
        db_path.write_bytes(b"this is not a sqlite database at all")
        mon._poll_worker()                       # 重连读到垃圾 → 优雅降级
        assert mon._db is None
        assert mon._db_ready is False
        assert received == ["thinking"]          # 无新事件

        # 删除：文件不存在时空转，不抛异常
        db_path.unlink()
        mon._poll_worker()
        assert mon._db is None

        # 重建新库：自动重连 + backfill（历史不回放），随后增量正常
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.updated.1', "
                   "'{\"info\":{\"role\":\"user\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()                       # 重连 + backfill
        assert received == ["thinking"]          # 历史不回放
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.part.updated.1', "
                   "'{\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()
        assert received == ["thinking", "working"]
        mon.stop()

    def test_opencode_subagent_filter_applies_in_background_poll(self, tmp_path):
        """子代理会话过滤保留在读取层：后台轮询路径同样过滤，不发信号。"""
        import sqlite3

        app = self._app()
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
        db.execute("INSERT INTO session VALUES ('root1', NULL)")
        db.execute("INSERT INTO session VALUES ('child1', 'root1')")
        db.commit()
        db.close()

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        try:
            mon._poll()                        # backfill
            assert mon._worker_done.wait(5)

            db = sqlite3.connect(db_path)
            db.execute("INSERT INTO event VALUES ('c', 1, 'message.part.updated.1', "
                       "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-start\"}}')")
            db.commit()
            db.close()
            mon._poll()                        # 子代理事件：读取层过滤
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == []              # 后台路径同样静默

            db = sqlite3.connect(db_path)
            db.execute("INSERT INTO event VALUES ('r', 2, 'message.part.updated.1', "
                       "'{\"sessionID\":\"root1\",\"part\":{\"type\":\"step-start\"}}')")
            db.commit()
            db.close()
            mon._poll()                        # 主会话正常报
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == ["working"]
        finally:
            mon.stop()


# ============================================================================
# B9 复审：生命周期隔离（迟到信号代次 / 双 worker / pause 竞态 / 关闭路径）
# 对应 _plan/REVIEW_B9_FINDINGS.md 的 P1/P2 逐项回归
# ============================================================================
class TestMonitorLifecycleIsolation:
    """B9 复审硬约束（全部事件/屏障同步，不用 sleep 猜时序）：

    1. stop 后已排队的迟到信号按代次隔离——快速 stop/start 时旧代事件
       不得污染新代 GUI 状态（含 stop 后不重启的场景）；
    2. pause 与在飞 worker 竞态不丢事件：暂停期间 worker 不读取
       （offset 不前移），已读取的在途事件由 manager 缓存、resume 重放；
    3. stop 超时后 start 必须等旧 worker 彻底退出才换代——绝不允许
       两个 worker 同时读写同一 tailer/SQLite；旧代 worker 不得关闭
       新代 worker 的 SQLite 连接；
    4. 旧窗口/角色切换/应用退出必须停掉 worker（manager.stop() 幂等、
       PetWindow.closeEvent、PetApp.switch_character、_on_about_to_quit）；
    5. OpenCode session 归属查询失败 fail-closed：子代理事件不得放行。
    """

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    @staticmethod
    def _write_event(mon, line: str) -> None:
        with open(mon.events_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---------------------------------------------------------------- 1. 迟到信号代次隔离
    def test_stop_start_drops_old_generation_queued_signals(self, tmp_path):
        """worker emit → 不跑事件循环 → stop → start（换代）→ processEvents：
        旧代已排队的信号必须被代次闸门拦截，只有新代事件生效。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s: received.append((k, s)))
        mon.start()
        try:
            mon._poll()                       # 旧代 backfill
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()                       # 旧代 worker 读取并发出信号（排队中）
            assert mon._worker_done.wait(5)
            mon.stop()                        # 不跑 processEvents：信号留在队列
            first = mon._worker_thread
            mon.start()                       # 换代：新代 worker
            assert mon._worker_thread is not first
            mon._poll()                       # 新代 backfill（跳过旧行）
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "Stop"}')
            mon._poll()                       # 新代读取新事件
            assert mon._worker_done.wait(5)
            app.processEvents()               # 旧代迟到信号 + 新代信号一起投递
            assert received == [("dsh", "attention")]   # 旧代 working 被隔离
        finally:
            mon.stop()

    def test_stop_without_restart_drops_queued_signals(self, tmp_path):
        """worker emit → 不跑事件循环 → stop（不重启）→ processEvents：
        已停监视器的迟到信号同样被隔离（运行态闸门）。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s: received.append((k, s)))
        mon.start()
        try:
            mon._poll()                       # backfill
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()                       # worker 读取并发出信号（排队中）
            assert mon._worker_done.wait(5)
            mon.stop()                        # 不 processEvents
            app.processEvents()               # 迟到信号投递
            assert received == []             # 已停监视器的信号被隔离
        finally:
            mon.stop()

    def test_emit_with_stale_generation_is_silent(self, tmp_path):
        """代次闸门：旧代 worker（换代后仍在跑）的发射路径全部静默。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got = []
        mon.state_changed.connect(lambda k, s: got.append(s))
        mon._running = True
        mon._generation = 3                   # 当前代次 3
        mon._poll_worker(gen=1)               # 旧代 poll：不得触碰共享状态/发信号
        mon._emit_state("working", gen=1)     # 旧代发射：静默
        mon._emit_activity("bash", gen=1)
        assert got == []
        mon._emit_state("working", gen=3)     # 当前代次：正常
        assert got == ["working"]

    # ---------------------------------------------------------------- 2. pause 竞态不丢事件
    def test_pause_buffers_in_flight_events_resume_replays(self, tmp_path):
        """pause 与在飞 worker 竞态：暂停期间到达的事件必须缓存并在
        resume 重放——绝不允许「offset 已推进但事件被丢」。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        received, bubbles = [], []

        class Win:
            cats = {"acts": ["写代码"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0
            _pending_link_anim = None

            def isVisible(self):
                return True

            def request_link_anim(self, name):
                received.append(name)

            def request_link_idle(self):
                pass

            def _switch(self, name):
                pass

            def _pick(self, lst):
                return lst[0]

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        mgr = AgentLinkManager(Win(), cfg, min_interval=0.0)
        mon = mgr.monitors["dsh"]
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()               # 先建空文件，backfill 才能落到末尾
        mon.start()
        try:
            mon._poll()                       # backfill
            assert mon._worker_done.wait(5)
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._poll()                       # worker 读取并发出信号（排队中）
            assert mon._worker_done.wait(5)
            mgr.pause()                       # 暂停：事件尚未投递
            app.processEvents()               # 投递 hop1：worker → 监视器代次闸门
            app.processEvents()               # 投递 hop2：监视器 → manager（暂停中 → 缓存）
            assert received == []
            assert "dsh" in mgr._paused_pending
            mgr.resume()                      # 恢复：重放缓存事件
            assert received == ["写代码"]     # 事件不丢
        finally:
            mgr.stop()

    def test_pause_freezes_worker_reads_resume_continues(self, tmp_path):
        """暂停期间 worker 不读取（offset 不前移），暂停期间写入的事件
        在 resume 后读到——「事件要么不读要么不丢」。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        try:
            mon._poll()                       # backfill
            assert mon._worker_done.wait(5)
            mon.pause()
            self._write_event(mon, '{"event": "PreToolUse"}')
            mon._worker_done.clear()
            mon._worker_tick.set()            # 即使手动唤醒，worker 也因暂停不读取
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == []
            mon.resume()
            mon._poll()
            assert mon._worker_done.wait(5)
            app.processEvents()
            assert received == ["working"]    # 暂停期间写入的事件恢复后读到
        finally:
            mon.stop()

    # ---------------------------------------------------------------- 3. 双 worker 防护
    def test_stop_timeout_never_leaves_two_workers(self, tmp_path, monkeypatch):
        """stop 超时（旧 worker 卡在 I/O）后 start 必须等旧 worker 彻底退出
        才换代——绝不允许新旧两个 worker 同时读写共享状态（审查 P1）。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._STOP_JOIN_TIMEOUT_S = 0.05        # 测试提速：stop 快速超时

        gate = threading.Event()               # 卡住 poll（模拟慢 I/O）
        entered = threading.Event()            # 旧 worker 已进入 poll
        in_poll = []                           # 当前并发 poll 的线程名
        peak = [0]
        guard = threading.Lock()
        orig = BaseAgentMonitor._poll_worker

        def blocking_poll(self_, gen=None):
            with guard:
                in_poll.append(threading.current_thread().name)
                peak[0] = max(peak[0], len(in_poll))
            entered.set()
            try:
                assert gate.wait(10), "测试 gate 超时"
            finally:
                with guard:
                    in_poll.remove(threading.current_thread().name)

        monkeypatch.setattr(BaseAgentMonitor, "_poll_worker", blocking_poll)
        mon.start()
        try:
            mon._poll()
            assert entered.wait(5)             # 旧 worker 卡在 I/O 中
            mon.stop()                         # join 超时：旧 worker 未退出
            old_thread = mon._worker_thread
            assert old_thread.is_alive()
            # 旧 worker 的 I/O 在 ~3.5s 后才放行：旧实现 start() 内部 join
            # 固定 2s，超时后会创建第二个 worker（≈2s 返回）；新实现无限期等
            # 旧 worker 死透（≥3.5s 才返回）。elapsed 断言区分两种实现，
            # 对确定性的 2s join 超时留有 1.5s 裕度。
            threading.Thread(
                target=lambda: (time.sleep(3.5), gate.set()), daemon=True,
            ).start()
            t0 = time.monotonic()
            mon.start()
            elapsed = time.monotonic() - t0
            assert elapsed >= 3.0, "start() 在旧 worker 死透前返回：允许双 worker 并发"
            assert not old_thread.is_alive()
            assert mon._worker_thread is not old_thread
            assert peak[0] == 1                # 全程从未有两个 worker 并发 poll
        finally:
            gate.set()
            mon.stop()

    def test_stale_generation_cannot_close_current_db(self, tmp_path):
        """旧代 worker 收尾不得关闭新代 worker 正在使用的 SQLite 连接
        （连接按 worker 代次私有，审查 P1）。"""
        import sqlite3

        app = self._app()
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.commit()
        db.close()
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon._running = True
        mon._generation = 2                   # 模拟新代 worker 已就位
        mon._poll_worker(gen=2)               # 新代建立只读连接
        assert mon._db is not None
        conn = mon._db
        mon._close_worker_resources(gen=1)    # 旧代收尾：不得关闭新代连接
        assert mon._db is conn
        mon._poll_worker(gen=1)               # 旧代 poll：代次不匹配，不触碰共享状态
        assert mon._db is conn
        mon._close_worker_resources(gen=2)    # 新代正常收尾
        assert mon._db is None

    # ---------------------------------------------------------------- 4. 关闭/切换路径
    def test_manager_stop_stops_all_monitor_workers(self, tmp_path):
        """manager.stop()（窗口关闭/角色切换/退出统一收尾）：所有监视器
        worker 线程退出，幂等可重复调用。"""
        app = self._app()
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag.update({"dsh": True, "claude": True, "cursor": True, "opencode": True})
        cfg.set("agent_link", ag)
        cfg.save()
        mgr = AgentLinkManager(None, cfg)
        try:
            for mon in mgr.monitors.values():
                assert mon._running is True
                assert mon._worker_thread is not None and mon._worker_thread.is_alive()
            mgr.stop()
            for mon in mgr.monitors.values():
                assert not mon._worker_thread.is_alive()
                assert mon._running is False
            mgr.stop()                        # 幂等
            for mon in mgr.monitors.values():
                assert not mon._worker_thread.is_alive()
        finally:
            mgr.stop()

    def test_pet_window_close_stops_agent_workers(self, tmp_path):
        """审查 P1：PetWindow.closeEvent 必须停止 agent_link_manager（worker 退出）。"""
        from pet.library import MovieLibrary
        from pet.window import PetWindow

        app = self._app()
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["dsh"] = True
        cfg.set("agent_link", ag)
        cfg.save()
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)
        try:
            mgr = win.agent_link_manager
            mon = mgr.monitors["dsh"]
            assert mon._running is True
            worker = mon._worker_thread
            assert worker is not None and worker.is_alive()
            win.close()
            app.processEvents()
            assert not worker.is_alive()
            assert mon._running is False
        finally:
            win.close()
            win.deleteLater()
            app.processEvents()

    def test_switch_character_stops_old_window_manager(self, tmp_path, monkeypatch):
        """审查 P1：switch_character 必须在旧窗口 deleteLater 前停止旧 manager
        （否则 worker 反向持有旧窗口引用链继续轮询）。"""
        from types import SimpleNamespace

        import pet.app as app_mod
        from pet.app import PetApp
        from pet.config import Config

        app = self._app()
        config = Config(base=tmp_path)
        config.set("character", "shenshen")
        config.save()
        owner = PetApp(app, config, enable_chat=False)

        stopped = []

        class FakeMgr:
            def stop(self):
                stopped.append(1)

        class FakeWin:
            agent_link_manager = FakeMgr()

            def detach_collision_session(self):
                pass

            def hide(self, notify=False):
                pass

            def show(self):
                pass

            def deleteLater(self):
                pass

        class FakeTray:
            def hide(self):
                pass

            def deleteLater(self):
                pass

        owner.win = FakeWin()
        owner.tray = FakeTray()
        owner.island = None
        monkeypatch.setattr(owner.collision_ipc, "stop", lambda: None)
        monkeypatch.setattr(
            app_mod, "CollisionIpcSession",
            lambda cfg, parent: SimpleNamespace(stop=lambda: None, start=lambda: None),
        )
        monkeypatch.setattr(
            app_mod, "PetWindow", lambda lib, cfg, collision_session=None: FakeWin(),
        )
        monkeypatch.setattr(owner, "_create_library", lambda cid: object())
        monkeypatch.setattr(owner, "_build_tray", lambda win: FakeTray())
        monkeypatch.setattr(app_mod, "warm_click_sound_effects", lambda *a, **k: None)

        owner.switch_character("another")
        assert stopped == [1]                 # 旧窗口 manager 已停止
        owner.switch_character("third")       # 再次切换：新旧窗口同样收尾
        assert stopped == [1, 1]
        app.processEvents()                   # 处理 deleteLater 调度（无异常即可）

    def test_about_to_quit_stops_current_window_manager(self, tmp_path, monkeypatch):
        """审查 P1：aboutToQuit 收尾必须停止当前窗口的 agent_link_manager。"""
        from pet.app import PetApp
        from pet.config import Config

        app = self._app()
        config = Config(base=tmp_path)
        owner = PetApp(app, config, enable_chat=False)
        stopped = []

        class FakeMgr:
            def stop(self):
                stopped.append(1)

        class FakeWin:
            agent_link_manager = FakeMgr()

            def _save_position(self):
                pass

        owner.win = FakeWin()
        owner.slot_handle = None
        monkeypatch.setattr(owner.collision_ipc, "stop", lambda: None)
        owner._on_about_to_quit()
        assert stopped == [1]

    # ---------------------------------------------------------------- 5. OpenCode fail-closed
    def test_opencode_session_query_failure_filters_batch(self, tmp_path):
        """审查 P2：有 session 表但归属查询失败（锁定/轮换/schema 变更中）时
        fail-closed——跳过整批并关闭连接，绝不把子代理事件当主会话放行
        （防「干完活啦」刷屏）；恢复后重连 backfill 正常。"""
        import sqlite3

        class _FlakyExecute:
            """代理真实连接：命中 fail 子串的 execute 抛异常（模拟归属查询失败）。"""

            def __init__(self, conn, fail_on_substring):
                self._conn = conn
                self._fail_on = fail_on_substring

            def execute(self, sql, params=()):
                if self._fail_on in sql:
                    raise sqlite3.OperationalError("simulated session query lock")
                return self._conn.execute(sql, params)

            def close(self):
                self._conn.close()

        app = self._app()
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
        db.execute("INSERT INTO session VALUES ('child1', 'root1')")
        db.commit()
        db.close()
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        received = []
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon._running = True
        mon._poll_worker()                    # 连接 + backfill

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('c', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        real_conn = mon._db
        mon._db = _FlakyExecute(real_conn, "FROM session")
        mon._poll_worker()                    # 归属查询失败 → 跳过整批
        assert received == []                 # 子代理事件未放行
        assert mon._db is None                # 连接已关闭，等待重连

        mon._poll_worker()                    # 重连 + backfill（跳过已读 batch）
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('r', 2, 'message.part.updated.1', "
                   "'{\"sessionID\":\"root1\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll_worker()                    # 主会话正常上报
        assert received == ["working"]
        mon._close_worker_resources()         # 同步 seam 手动收尾
