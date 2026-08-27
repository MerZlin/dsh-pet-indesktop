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

from pet.agent_link import (
    AgentLinkManager,
    ByteOffsetTailer,
    ClaudeCodeMonitor,
    CursorMonitor,
    normalize_event_state,
)
from pet.config import Config


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
                self.cats = {"acts": ["写代码", "原地敲击桌面互动"]}
                self.idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched_anims.append(name)

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        win = DummyPetWindow()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)  # 测试关闭节流，逐个验证状态映射

        # 模拟 Agent 状态分发
        mgr._on_agent_state("claude", "thinking")
        assert "写代码" in switched_anims

        mgr._on_agent_state("claude", "working")
        assert "原地敲击桌面互动" in switched_anims

        mgr._on_agent_state("claude", "attention")
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
        """重复安装不产生重复条目；用户自己的 hooks 原样保留。"""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]}]},
            "other_key": 1,
        }), encoding="utf-8")
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        ClaudeCodeMonitor.install_hooks(events_file)  # 重复安装

        data = json.loads(settings.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        ours = [g for g in entries if "claude_event_hook" in json.dumps(g)]
        theirs = [g for g in entries if "my-own-hook" in json.dumps(g)]
        assert len(ours) == 1  # 幂等
        assert len(theirs) == 1  # 用户的保留
        assert data["other_key"] == 1

    def test_uninstall_removes_only_ours(self, tmp_path, monkeypatch):
        settings = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "user-cmd"}]},
            ]},
        }), encoding="utf-8")

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        assert ClaudeCodeMonitor.uninstall_hooks() is True

        data = json.loads(settings.read_text(encoding="utf-8"))
        stop_entries = data["hooks"]["Stop"]
        assert all("claude_event_hook" not in json.dumps(g) for g in stop_entries)
        assert any("user-cmd" in json.dumps(g) for g in stop_entries)
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
            cats = {"acts": ["写代码", "原地敲击桌面互动"]}
            idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

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
        assert switched == ["原地敲击桌面互动"]  # 只切一次

    def test_throttled_within_interval(self, tmp_path):
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 1.0  # 1s < 2s 节流间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["原地敲击桌面互动"]  # 被节流
        clock[0] += 2.0  # 超过间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["原地敲击桌面互动", "写代码"]


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
