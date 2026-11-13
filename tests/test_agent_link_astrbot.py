# -*- coding: utf-8 -*-
"""AstrBot 内置联动通道：配置、桥接插件安装/卸载、监视器读取与管理器编排。

写方是 ``integrations/astrbot-pet-bridge/main.py``（AstrBot 插件），读方是
``pet.agent_link.AstrBotMonitor``。两侧对事件文件位置的计算必须一致——这里用
**安装后的插件文件**反推路径来证明，而不是两边各写一遍常量。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import pet.agent_link as agent_link
from pet.agent_link import AgentLinkManager, AstrBotMonitor
from pet.config import Config, _clean_agent_link_data, _clean_custom_agents


@pytest.fixture(autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _astrbot_root(tmp_path: Path) -> Path:
    root = tmp_path / "AstrBot"
    (root / "data" / "plugins").mkdir(parents=True)
    (root / "data" / "cmd_config.json").write_text("{}", encoding="utf-8")
    return root


def _load_installed_plugin(plugin_dir: Path):
    spec = importlib.util.spec_from_file_location("astrbot_installed_bridge", plugin_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("astrbot", None)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- 配置
class TestAstrBotConfig:
    def test_defaults_off_with_empty_root(self, tmp_path):
        cfg = Config(base=tmp_path)
        assert cfg.data["agent_link"]["astrbot"] is False
        assert cfg.data["agent_link"]["astrbot_root"] == ""

    def test_clean_coerces_types(self):
        cleaned = _clean_agent_link_data({"astrbot": 1, "astrbot_root": "  ~/AstrBot/data  "})
        assert cleaned["astrbot"] is True
        assert cleaned["astrbot_root"] == "~/AstrBot/data"
        assert _clean_agent_link_data({"astrbot_root": None})["astrbot_root"] == ""
        assert len(_clean_agent_link_data({"astrbot_root": "x" * 900})["astrbot_root"]) == 500

    def test_custom_agent_cannot_shadow_builtin_key(self):
        assert _clean_custom_agents([{"key": "astrbot", "path": "~/a.jsonl"}]) == []


# --------------------------------------------------------------- 目录识别
class TestDataDirResolution:
    def test_accepts_root_or_data_dir(self, tmp_path):
        root = _astrbot_root(tmp_path)
        assert AstrBotMonitor.normalize_data_dir(root) == root / "data"
        assert AstrBotMonitor.normalize_data_dir(root / "data") == root / "data"
        assert AstrBotMonitor.normalize_data_dir(str(root)) == root / "data"

    def test_rejects_unrelated_dirs(self, tmp_path):
        assert AstrBotMonitor.normalize_data_dir(tmp_path) is None
        assert AstrBotMonitor.normalize_data_dir(tmp_path / "missing") is None
        assert AstrBotMonitor.normalize_data_dir("") is None
        assert AstrBotMonitor.normalize_data_dir(None) is None

    def test_detect_prefers_astrbot_root_env(self, tmp_path, monkeypatch):
        root = _astrbot_root(tmp_path)
        monkeypatch.setenv("ASTRBOT_ROOT", str(root))
        assert AstrBotMonitor.detect_data_dir() == root / "data"

    def test_detect_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert AstrBotMonitor.detect_data_dir() is None


# --------------------------------------------------------------- 安装 / 卸载
class TestBridgeInstall:
    def test_install_copies_bundle_with_marker(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        ok, msg = AstrBotMonitor.install_bridge(data)
        assert ok, msg
        target = data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME
        assert (target / "main.py").is_file()
        assert (target / "metadata.yaml").is_file()
        assert (target / AstrBotMonitor.MARKER_NAME).is_file()

    def test_installed_plugin_and_monitor_agree_on_events_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DSH_PET_ASTRBOT_EVENTS", raising=False)
        data = _astrbot_root(tmp_path) / "data"
        AstrBotMonitor.install_bridge(data)
        plugin = _load_installed_plugin(data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME)
        writer_path = plugin.default_events_path(
            data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME / "main.py")
        assert writer_path == AstrBotMonitor.events_file_for(data)

    def test_reinstall_overwrites_own_copy(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        AstrBotMonitor.install_bridge(data)
        target = data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME
        (target / "main.py").write_text("# stale", encoding="utf-8")
        ok, _ = AstrBotMonitor.install_bridge(data)
        assert ok
        assert "PetBridge" in (target / "main.py").read_text(encoding="utf-8")

    def test_refuses_to_overwrite_foreign_plugin(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        foreign = data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME
        foreign.mkdir()
        (foreign / "main.py").write_text("# user's own", encoding="utf-8")
        ok, msg = AstrBotMonitor.install_bridge(data)
        assert not ok and msg
        assert (foreign / "main.py").read_text(encoding="utf-8") == "# user's own"
        assert AstrBotMonitor.uninstall_bridge(data) is False
        assert foreign.is_dir()

    def test_install_rejects_invalid_dir(self, tmp_path):
        ok, msg = AstrBotMonitor.install_bridge(tmp_path / "nope")
        assert not ok and msg

    def test_uninstall_removes_plugin_and_events(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        AstrBotMonitor.install_bridge(data)
        events = AstrBotMonitor.events_file_for(data)
        events.parent.mkdir(parents=True)
        events.write_text('{"state":"idle"}\n', encoding="utf-8")
        events.with_name(events.name + ".1").write_text("", encoding="utf-8")
        assert AstrBotMonitor.uninstall_bridge(data) is True
        assert not (data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME).exists()
        assert not events.parent.exists()
        assert AstrBotMonitor.uninstall_bridge(data) is True  # 幂等

    def test_uninstall_keeps_unknown_files_in_events_dir(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        events = AstrBotMonitor.events_file_for(data)
        events.parent.mkdir(parents=True)
        events.write_text("", encoding="utf-8")
        (events.parent / "user-note.txt").write_text("keep", encoding="utf-8")
        assert AstrBotMonitor.uninstall_bridge(data) is True
        assert (events.parent / "user-note.txt").is_file()
        assert not events.exists()


# --------------------------------------------------------------- 监视器读取
class TestAstrBotMonitorTail:
    def test_reads_bridge_output_end_to_end(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DSH_PET_ASTRBOT_EVENTS", raising=False)
        data = _astrbot_root(tmp_path) / "data"
        AstrBotMonitor.install_bridge(data)
        plugin = _load_installed_plugin(data / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME)
        bridge = plugin.PetBridge(AstrBotMonitor.events_file_for(data))
        bridge.reset()  # 插件加载时的那条 idle：发生在桌宠读方初始化之前（回填跳过）

        mon = AstrBotMonitor(tmp_path / "pet-config", data_dir=data)
        states, tools = [], []
        mon.state_changed.connect(lambda k, s: states.append((k, s)))
        mon.activity.connect(lambda k, t: tools.append((k, t)))
        # 不让后台 worker 与下面的直调 _poll 争抢 tailer（CI 慢机上 1.5s 首拍可能先到）
        monkeypatch.setattr(mon, "_work_loop", lambda gen: None)
        assert mon.start() is not False
        try:
            mon._poll()  # 回填：跳过历史
            bridge.begin_turn("e1")
            bridge.tool_start("e1", "web_search")
            bridge.message_sent("e1")
            mon._poll()
        finally:
            mon.stop()
        assert states == [("astrbot", "thinking"), ("astrbot", "working"), ("astrbot", "idle")]
        assert tools == [("astrbot", "web_search")]

    def test_start_never_creates_dirs_inside_astrbot(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        mon = AstrBotMonitor(tmp_path / "pet-config", data_dir=data)
        mon.start()
        try:
            assert not (data / "plugin_data").exists()
        finally:
            mon.stop()

    def test_set_data_dir_retargets_tailer(self, tmp_path):
        data = _astrbot_root(tmp_path) / "data"
        mon = AstrBotMonitor(tmp_path / "pet-config")
        assert mon.events_file is None or mon.data_dir is None
        mon.set_data_dir(data)
        assert mon.data_dir == data
        assert mon.events_file == AstrBotMonitor.events_file_for(data)


# --------------------------------------------------------------- 管理器编排
class _Win:
    def __init__(self):
        self.bubbles = []

    def show_bubble(self, text, duration_ms=3000, **kw):
        self.bubbles.append(text)


class TestManagerEnable:
    def test_registered_as_builtin(self, tmp_path):
        mgr = AgentLinkManager(None, Config(base=tmp_path))
        assert isinstance(mgr.monitors["astrbot"], AstrBotMonitor)
        assert AgentLinkManager.AGENT_NAMES["astrbot"] == "AstrBot"

    def test_enable_picks_dir_installs_and_starts(self, tmp_path, monkeypatch):
        root = _astrbot_root(tmp_path)
        monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
        monkeypatch.setattr(AstrBotMonitor, "detect_data_dir", staticmethod(lambda: None))
        picked = []
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            lambda *a, **k: picked.append(a) or str(root))
        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)
        cfg = Config(base=tmp_path / "cfg")
        win = _Win()
        mgr = AgentLinkManager(win, cfg)
        try:
            assert mgr.set_enabled("astrbot", True) is True
            assert picked and asked
            # 同意框必须写明要写入的具体目录
            assert any(str(root / "data" / "plugins") in str(arg) for arg in asked[0])
            ag = cfg.data["agent_link"]
            assert ag["astrbot"] is True
            assert ag["astrbot_root"] == str(root / "data")
            assert (root / "data" / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME / "main.py").is_file()
            mon = mgr.monitors["astrbot"]
            assert mon._running
            assert mon.events_file == AstrBotMonitor.events_file_for(root / "data")
            assert "重启 AstrBot" in mgr.enable_notice("astrbot")
            saved = json.loads(cfg.path.read_text(encoding="utf-8"))
            assert saved["agent_link"]["astrbot_root"] == str(root / "data")
        finally:
            mgr.shutdown()

    @pytest.mark.parametrize("consent", [True, False])
    def test_detected_root_skips_picker_and_respects_consent(self, tmp_path, monkeypatch, consent):
        data = _astrbot_root(tmp_path) / "data"
        monkeypatch.setattr(AstrBotMonitor, "detect_data_dir", classmethod(lambda cls: data))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            lambda *a, **k: pytest.fail("自动检测成功后不应弹目录选择"))
        asked = []
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: asked.append(a) or (
                QMessageBox.StandardButton.Yes if consent else QMessageBox.StandardButton.No),
        )
        cfg = Config(base=tmp_path / "cfg")
        cfg.save()
        before = cfg.path.read_bytes()
        mgr = AgentLinkManager(None, cfg)
        try:
            assert mgr.set_enabled("astrbot", True) is consent
            assert len(asked) == 1
            assert str(AstrBotMonitor.plugin_dir_for(data)) in asked[0][2]
            assert cfg.data["agent_link"]["astrbot"] is consent
            assert mgr.monitors["astrbot"]._running is consent
            assert AstrBotMonitor.plugin_dir_for(data).exists() is consent
            saved = json.loads(cfg.path.read_text(encoding="utf-8"))
            assert saved["agent_link"]["astrbot_root"] == (str(data) if consent else "")
            if not consent:
                assert cfg.path.read_bytes() == before
        finally:
            mgr.shutdown()

    def test_configured_root_skips_picker(self, tmp_path, monkeypatch):
        root = _astrbot_root(tmp_path)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            lambda *a, **k: pytest.fail("不应再弹目录选择"))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        cfg = Config(base=tmp_path / "cfg")
        ag = dict(cfg.get("agent_link", {}))
        ag["astrbot_root"] = str(root)
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(None, cfg)
        try:
            assert mgr.set_enabled("astrbot", True) is True
        finally:
            mgr.shutdown()

    def test_cancel_picker_or_decline_changes_nothing(self, tmp_path, monkeypatch):
        root = _astrbot_root(tmp_path)
        monkeypatch.setattr(AstrBotMonitor, "detect_data_dir", staticmethod(lambda: None))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
        cfg = Config(base=tmp_path / "cfg")
        mgr = AgentLinkManager(None, cfg)
        try:
            assert mgr.set_enabled("astrbot", False) is True  # 关闭本就关着：无副作用
            assert mgr.set_enabled("astrbot", True) is False
            monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(root))
            monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
            assert mgr.set_enabled("astrbot", True) is False
            assert cfg.data["agent_link"]["astrbot"] is False
            assert not (root / "data" / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME).exists()
            assert not mgr.monitors["astrbot"]._running
        finally:
            mgr.shutdown()

    def test_invalid_pick_warns_and_aborts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AstrBotMonitor, "detect_data_dir", staticmethod(lambda: None))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
        warned = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: pytest.fail("无效目录不应进入授权"))
        mgr = AgentLinkManager(None, Config(base=tmp_path / "cfg"))
        try:
            assert mgr.set_enabled("astrbot", True) is False
            assert warned
        finally:
            mgr.shutdown()

    def test_disable_uninstalls_bridge(self, tmp_path, monkeypatch):
        root = _astrbot_root(tmp_path)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        cfg = Config(base=tmp_path / "cfg")
        ag = dict(cfg.get("agent_link", {}))
        ag["astrbot_root"] = str(root / "data")
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(None, cfg)
        try:
            assert mgr.set_enabled("astrbot", True)
            assert mgr.set_enabled("astrbot", False) is True
            assert not (root / "data" / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME).exists()
            assert not mgr.monitors["astrbot"]._running
            assert cfg.data["agent_link"]["astrbot_root"] == str(root / "data")  # 记住目录
        finally:
            mgr.shutdown()

    def test_disable_keeps_bridge_when_other_instance_uses_it(self, tmp_path, monkeypatch):
        cfg = Config(base=tmp_path / "cfg")
        mgr = AgentLinkManager(None, cfg)
        cfg.dir.mkdir(parents=True, exist_ok=True)
        (cfg.dir / "config-pet2.json").write_text(
            json.dumps({"agent_link": {"astrbot": True}}), encoding="utf-8")
        calls = []
        monkeypatch.setattr(AstrBotMonitor, "uninstall_bridge",
                            staticmethod(lambda *a: calls.append(a) or True))
        try:
            assert mgr.set_enabled("astrbot", False) is True
            assert calls == []
        finally:
            mgr.shutdown()

    def test_apply_config_starts_with_saved_root(self, tmp_path):
        root = _astrbot_root(tmp_path)
        cfg = Config(base=tmp_path / "cfg")
        ag = dict(cfg.get("agent_link", {}))
        ag.update({"astrbot": True, "astrbot_root": str(root)})
        cfg.set("agent_link", ag)
        mgr = AgentLinkManager(None, cfg)
        try:
            mon = mgr.monitors["astrbot"]
            assert mon._running
            assert mon.events_file == AstrBotMonitor.events_file_for(root / "data")
        finally:
            mgr.shutdown()


# --------------------------------------------------------------- 入口面
def test_menu_lists_astrbot_toggle(tmp_path):
    from PySide6.QtWidgets import QMenu
    from pet.context_menus.shared import add_agent_link_menu

    toggles = []

    class DummyPet:
        cfg = Config(base=tmp_path)

        def toggle_agent_link(self, key, on, action=None):
            toggles.append((key, on))

    menu = QMenu()
    try:
        add_agent_link_menu(menu, DummyPet())
        sub = menu.actions()[0].menu()
        act = next(a for a in sub.actions() if a.text() == "AstrBot")
        act.setChecked(True)
        assert toggles == [("astrbot", True)]
    finally:
        import shiboken6
        shiboken6.delete(menu)


def test_feature_spec_owns_astrbot_keys():
    from pet.features import FEATURES

    spec = FEATURES["agent_link"]
    assert "agent_link.astrbot" in spec.config_keys
    assert "agent_link.astrbot_root" in spec.config_keys
    assert "AstrBot" in spec.desc


def test_uninstall_cleanup_removes_astrbot_bridge(tmp_path, monkeypatch):
    from pet import autostart, uninstall_cleanup

    monkeypatch.setattr(autostart, "disable", lambda: True)
    monkeypatch.setattr(agent_link.ClaudeCodeMonitor, "uninstall_hooks", classmethod(lambda cls: True))
    monkeypatch.setattr(agent_link.DshMonitor, "uninstall_bridge", classmethod(lambda cls: True))
    monkeypatch.setattr(uninstall_cleanup.ClaudeCodeMonitor, "uninstall_hooks", classmethod(lambda cls: True))
    monkeypatch.setattr(uninstall_cleanup.DshMonitor, "uninstall_bridge", classmethod(lambda cls: True))
    root = _astrbot_root(tmp_path)
    AstrBotMonitor.install_bridge(root / "data")
    cfg = Config(base=tmp_path / "cfg")
    ag = dict(cfg.get("agent_link", {}))
    ag["astrbot_root"] = str(root / "data")
    cfg.set("agent_link", ag)
    results = uninstall_cleanup.run_uninstall_cleanup(cfg)
    assert results["astrbot_bridge"] is True
    assert not (root / "data" / "plugins" / AstrBotMonitor.PLUGIN_DIR_NAME).exists()


def test_uninstall_cleanup_without_root_is_noop(tmp_path, monkeypatch):
    from pet import autostart, uninstall_cleanup

    monkeypatch.setattr(autostart, "disable", lambda: True)
    monkeypatch.setattr(uninstall_cleanup.ClaudeCodeMonitor, "uninstall_hooks", classmethod(lambda cls: True))
    monkeypatch.setattr(uninstall_cleanup.DshMonitor, "uninstall_bridge", classmethod(lambda cls: True))
    results = uninstall_cleanup.run_uninstall_cleanup(Config(base=tmp_path / "cfg"))
    assert results["astrbot_bridge"] is True
