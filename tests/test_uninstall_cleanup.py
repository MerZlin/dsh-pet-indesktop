# -*- coding: utf-8 -*-
"""卸载清理（--uninstall-cleanup）各步骤的可 mock 单元测试。

覆盖：
- run_uninstall_cleanup 依次执行自启删除 / Claude hooks 卸载 / DSH 桥接插件卸载；
- 其他实例仍使用 DSH 联动时桥接插件保留（skipped）；
- other_instances_use_agent 对同变体其他配置文件的识别；
- __main__ 对 --uninstall-cleanup 的派发。
"""

from __future__ import annotations

import json
import sys

from pet.config import Config
from pet import uninstall_cleanup


def _config(tmp_path) -> Config:
    return Config(base=tmp_path)


def test_uninstall_cleanup_runs_all_steps(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: True)
    monkeypatch.setattr(
        "pet.agent_link.ClaudeCodeMonitor.uninstall_hooks", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(
        "pet.agent_link.DshMonitor.uninstall_bridge", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(uninstall_cleanup, "other_instances_use_agent", lambda config, key: False)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is True
    assert results["claude_hooks"] is True
    assert results["dsh_bridge"] is True


def test_uninstall_cleanup_reports_step_failures(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: False)
    monkeypatch.setattr(
        "pet.agent_link.ClaudeCodeMonitor.uninstall_hooks", classmethod(lambda cls: False)
    )
    monkeypatch.setattr(
        "pet.agent_link.DshMonitor.uninstall_bridge", classmethod(lambda cls: False)
    )
    monkeypatch.setattr(uninstall_cleanup, "other_instances_use_agent", lambda config, key: False)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is False
    assert results["claude_hooks"] is False
    assert results["dsh_bridge"] is False


def test_uninstall_cleanup_skips_bridge_when_other_instance_uses_dsh(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: True)
    monkeypatch.setattr(
        "pet.agent_link.ClaudeCodeMonitor.uninstall_hooks", classmethod(lambda cls: True)
    )
    # 其他实例仍在使用 DSH 联动：桥接插件必须保留
    def fake_other_instances(cfg, key):
        return key == "dsh"

    monkeypatch.setattr(uninstall_cleanup, "other_instances_use_agent", fake_other_instances)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is True
    assert results["claude_hooks"] is True
    assert results["dsh_bridge"] == "skipped"


def test_uninstall_cleanup_skips_claude_when_other_instance_uses_claude(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: True)
    monkeypatch.setattr(
        "pet.agent_link.DshMonitor.uninstall_bridge", classmethod(lambda cls: True)
    )
    # 其他实例仍在使用 Claude 联动：hooks 必须保留
    def fake_other_instances(cfg, key):
        return key == "claude"

    monkeypatch.setattr(uninstall_cleanup, "other_instances_use_agent", fake_other_instances)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is True
    assert results["claude_hooks"] == "skipped"
    assert results["dsh_bridge"] is True


def test_uninstall_cleanup_deletes_claude_when_no_other_instance(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: True)
    monkeypatch.setattr(
        "pet.agent_link.ClaudeCodeMonitor.uninstall_hooks", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(
        "pet.agent_link.DshMonitor.uninstall_bridge", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(uninstall_cleanup, "other_instances_use_agent", lambda config, key: False)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is True
    assert results["claude_hooks"] is True
    assert results["dsh_bridge"] is True


def test_other_instances_use_agent_detects_peers(tmp_path):
    this_cfg = _config(tmp_path)
    this_cfg.data["agent_link"] = {"dsh": True, "claude": True}
    this_cfg.save()
    # 当前实例自身开启不视为“其他实例”
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "dsh") is False
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "claude") is False

    # 同变体其他实例开启 dsh → 识别为其他实例在用
    peer = this_cfg.dir / "config-abc.json"
    peer.write_text(json.dumps({"agent_link": {"dsh": True, "claude": False}}), encoding="utf-8")
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "dsh") is True
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "claude") is False

    # 同变体其他实例开启 claude
    peer2 = this_cfg.dir / "config-def.json"
    peer2.write_text(json.dumps({"agent_link": {"dsh": False, "claude": True}}), encoding="utf-8")
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "dsh") is True
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "claude") is True


def test_other_instances_use_agent_cross_variant(tmp_path):
    this_cfg = _config(tmp_path)
    # 另一个变体目录（如 webm）开启了 dsh——桥接插件是跨变体共享的，必须识别
    variant_dir = tmp_path / "dsh-pet-standalone-webm"
    variant_dir.mkdir()
    (variant_dir / "config.json").write_text(
        json.dumps({"agent_link": {"dsh": True}}), encoding="utf-8"
    )
    assert uninstall_cleanup.other_instances_use_agent(this_cfg, "dsh") is True


def test_main_dispatches_uninstall_cleanup(monkeypatch):
    import pet.__main__ as m
    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(
        "pet.uninstall_cleanup.run_uninstall_cleanup", lambda: {"autostart": True, "claude_hooks": True, "dsh_bridge": True}
    )
    assert m._main() == 0


def test_main_returns_nonzero_on_uninstall_failure(monkeypatch):
    import pet.__main__ as m
    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(
        "pet.uninstall_cleanup.run_uninstall_cleanup", lambda: {"autostart": False, "claude_hooks": "skipped", "dsh_bridge": True}
    )
    assert m._main() != 0
