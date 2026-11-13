# -*- coding: utf-8 -*-
"""卸载清理（--uninstall-cleanup）各步骤的可 mock 单元测试。

覆盖：
- run_uninstall_cleanup 依次执行自启删除 / Claude hooks 卸载 / DSH 桥接插件卸载；
- 其他实例仍使用 DSH 联动时桥接插件保留（skipped）；
- other_instances_use_agent 的逐类判定：本变体自己的 slot 档案不算“别的实例”，
  旧式独立实例配置 / peer 变体 / 旧共享目录照旧保护；
- __main__ 把 --uninstall-cleanup 派发给共享入口 pet.uninstall_cleanup.main。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pet.config import Config
from pet import uninstall_cleanup

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path) -> Config:
    return Config(base=tmp_path)


def _variant_config(tmp_path, variant: str) -> Config:
    """把“当前被卸载的变体”指到合成目录。

    源码树里 ``APP_DIR_NAME`` 没有变体后缀，真机上它由打包变体决定
    （``pet.config._app_dir_name``）；这里直接指过去——判定的消费面只有
    ``config.dir`` / ``config.path`` 两个属性。
    """
    config = _config(tmp_path)
    config.dir = tmp_path / variant
    config.path = config.dir / "config.json"
    config.dir.mkdir(parents=True, exist_ok=True)
    return config


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


# ------------------------------------- other_instances_use_agent：逐类判定

def test_slot_profiles_in_own_variant_are_not_other_instances(tmp_path):
    """本变体目录里的 ``config-slot-N.json`` 是固定身份的每宠档案，不是“别的实例”。

    旧版 ``slot_manager.seed_slot_config_from_main`` 把主配置整份落种进档案（含
    ``agent_link``），这类残留留在盘上时必须不采信：否则卸载会被本变体自己的旧档案
    挡住，全局的 hooks / 桥接插件清不掉、留下残留。
    """
    config = _config(tmp_path)
    for slot in (0, 3, 128):
        _write_json(
            config.dir / f"config-slot-{slot}.json",
            {"agent_link": {"dsh": True, "claude": True}, "scale": 0.8},
        )
    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is False
    assert uninstall_cleanup.other_instances_use_agent(config, "claude") is False

    # 正对照：同样的内容落在“非 slot 的独立实例配置”里就必须保护
    _write_json(config.dir / "config-lan.json", {"agent_link": {"dsh": True}})
    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is True
    assert uninstall_cleanup.other_instances_use_agent(config, "claude") is False


@pytest.mark.parametrize(
    "name",
    ["config-slot.json", "config-slot-x.json", "config-slots-1.json", "config-slot-1x.json"],
)
def test_near_miss_slot_names_still_protect(tmp_path, name):
    """只有 ``config-slot-<数字>.json`` 是档案；近形名字按独立实例配置保守保护。"""
    config = _config(tmp_path)
    _write_json(config.dir / name, {"agent_link": {"dsh": True}})

    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is True


@pytest.mark.parametrize(
    "name",
    ["config-slot-1.json.bak", "config.json.pre-slot0-migration.bak", "config.json.corrupt-1-2"],
)
def test_config_backups_are_not_read_as_instance_configs(tmp_path, name):
    """备份/损坏副本不在扫描面里（只看 ``config-*.json``），不会被当成实例配置读。"""
    config = _config(tmp_path)
    _write_json(config.dir / name, {"agent_link": {"dsh": True}})

    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is False


def test_own_global_config_is_self_not_another_instance(tmp_path):
    """自己那份配置（``config.path``）永远不算“别的实例”，即使里面写着开着。"""
    config = _config(tmp_path)
    _write_json(config.path, {"agent_link": {"dsh": True, "claude": True}})

    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is False
    assert uninstall_cleanup.other_instances_use_agent(config, "claude") is False


def test_empty_or_broken_config_dir_never_raises(tmp_path):
    """配置目录不存在 / 档案是坏 JSON / 档案位置被目录占着：都不抛异常，按不保护处理。"""
    missing = _config(tmp_path / "nothing-here")
    assert uninstall_cleanup.other_instances_use_agent(missing, "dsh") is False

    config = _config(tmp_path)
    config.dir.mkdir(parents=True, exist_ok=True)
    (config.dir / "config-slot-1.json").mkdir()  # 名字对但是个目录
    (config.dir / "config-slot-2.json").write_text("{not json", encoding="utf-8")
    (config.dir / "config.json").write_text("", encoding="utf-8")
    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is False

    # 正对照：目录坏了也不会把别的安装点看漏
    _write_json(tmp_path / "dsh-pet-standalone-webm" / "config.json", {"agent_link": {"dsh": True}})
    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is True


def test_uninstall_cleanup_removes_hooks_despite_own_slot_residue(tmp_path, monkeypatch):
    """本变体自己的 slot 档案残留不许挡住卸载：hooks 与桥接插件必须真的被卸掉。

    这是判定函数的真实消费面（``run_uninstall_cleanup`` → ``other_instances_use_agent``）
    端到端跑一次：只把自启/hooks/桥接换成 spy——不碰真实注册表、``~/.claude``、桥接目录。
    """
    config = _config(tmp_path)
    _write_json(
        config.dir / "config-slot-0.json",
        {"agent_link": {"dsh": True, "claude": True}, "scale": 0.8},
    )
    removed = []
    monkeypatch.setattr(
        "pet.autostart.disable", lambda: (removed.append("autostart"), True)[1]
    )
    monkeypatch.setattr(
        "pet.agent_link.ClaudeCodeMonitor.uninstall_hooks",
        classmethod(lambda cls: (removed.append("claude"), True)[1]),
    )
    monkeypatch.setattr(
        "pet.agent_link.DshMonitor.uninstall_bridge",
        classmethod(lambda cls: (removed.append("dsh"), True)[1]),
    )

    results = uninstall_cleanup.run_uninstall_cleanup(config)

    # astrbot_bridge：本配置没记过 AstrBot 目录 = 从未安装，无事可做也算完成
    assert results == {"autostart": True, "claude_hooks": True, "dsh_bridge": True,
                       "astrbot_bridge": True}
    assert removed == ["autostart", "claude", "dsh"]


def test_peer_variant_and_legacy_shared_dir_keep_protecting(tmp_path):
    """peer 变体与旧共享目录（历史安装）的配置照旧保护：跨变体共享的全局状态不许误删。"""
    config = _variant_config(tmp_path, "dsh-pet-standalone-webm-chat")
    _write_json(config.path, {"agent_link": {"dsh": False, "claude": False}})
    # 本变体自己的 slot 档案同时存在：不许影响 peer 判定
    _write_json(config.dir / "config-slot-2.json", {"agent_link": {"dsh": True, "claude": True}})

    peer = tmp_path / "dsh-pet-standalone-webm"
    legacy_shared = tmp_path / "dsh-pet-standalone"
    _write_json(peer / "config.json", {"agent_link": {"dsh": True, "claude": False}})
    _write_json(legacy_shared / "config-lan.json", {"agent_link": {"dsh": False, "claude": True}})

    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is True
    assert uninstall_cleanup.other_instances_use_agent(config, "claude") is True

    # 两个都是 false 之后才轮到不保护（正对照：保护来自真实别的安装点，不是本变体档案）
    _write_json(peer / "config.json", {"agent_link": {"dsh": False, "claude": False}})
    (legacy_shared / "config-lan.json").unlink()
    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is False
    assert uninstall_cleanup.other_instances_use_agent(config, "claude") is False


def test_peer_variant_slot_profiles_stay_conservative(tmp_path):
    """peer 变体目录的扫描面（含它的 slot 档案）保持改动前逐字一致。

    本次只收“本变体自己的档案”这一类误保护；别人的目录不动，方向偏保守——
    最多多保护一次，不会误删别的变体还在用的全局 hooks / 桥接。
    """
    config = _variant_config(tmp_path, "dsh-pet-standalone-webm-chat")
    _write_json(config.path, {"agent_link": {"dsh": False}})
    peer = tmp_path / "dsh-pet-standalone-webm"
    _write_json(peer / "config-slot-2.json", {"agent_link": {"dsh": True}})

    assert uninstall_cleanup.other_instances_use_agent(config, "dsh") is True


# ------------------------------------------- __main__：--uninstall-cleanup 派发

_DISPATCH_RUNNER = r"""
import json, runpy, sys, types

sys.path.insert(0, sys.argv[1])  # 仓库根：runpy 要能 import pet（子进程 cwd 不进 sys.path）
record = {"legacy": [], "main": [], "app": []}

uninstall = types.ModuleType("pet.uninstall_cleanup")


def run_uninstall_cleanup(*args, **kwargs):
    # 旧接线：入口自己复刻的清理调用（本次要替掉的那条路）
    record["legacy"].append([list(args), kwargs])
    return {"autostart": True, "claude_hooks": True, "dsh_bridge": True}


def main(*args, **kwargs):
    record["main"].append([list(args), kwargs])
    return 7


uninstall.run_uninstall_cleanup = run_uninstall_cleanup
uninstall.main = main

app = types.ModuleType("pet.app")


def app_main(*args, **kwargs):
    record["app"].append(list(sys.argv))
    return 99


app.main = app_main

import pet
pet.uninstall_cleanup = uninstall
pet.app = app
sys.modules["pet.uninstall_cleanup"] = uninstall
sys.modules["pet.app"] = app

sys.argv = ["dsh-pet-standalone", "--uninstall-cleanup"]
exit_code = 0
try:
    runpy.run_module("pet.__main__", run_name="__main__")
except SystemExit as exc:
    exit_code = exc.code if isinstance(exc.code, int) else 1

qt_loaded = sorted(n for n in sys.modules if n == "PySide6" or n.startswith("PySide6."))
print(json.dumps({
    "exit": exit_code,
    "legacy": record["legacy"],
    "main": record["main"],
    "app": record["app"],
    "qt": qt_loaded,
}))
"""


def test_main_entry_delegates_uninstall_cleanup_without_qt_or_desktop_main(tmp_path):
    """真实入口进程里跑 `--uninstall-cleanup`：走共享 main，不建 Qt、不跑桌宠 main。

    spy 经 ``sys.modules`` 注入（清理实现与 ``pet.app``）：即使入口漏了分流也只会
    调到 spy——不卸真实自启/hooks/桥接插件、不碰真实用户配置、不启动桌宠。
    """
    runner = tmp_path / "_pet_main_runner.py"
    runner.write_text(_DISPATCH_RUNNER, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(runner), str(ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload["main"] == [[[], {}]], "必须调用共享入口 main（无参）"
    assert payload["legacy"] == [], "入口不许自己复刻清理调用：逐步诊断/退出码只有一处实现"
    assert payload["exit"] == 7, "共享入口的退出码原样返回"
    assert payload["app"] == [], "分流后不许再跑正常桌宠 main"
    assert payload["qt"] == [], f"卸载清理路径不该加载 Qt：{payload['qt']}"


def test_main_entry_dispatches_before_importing_app():
    """静态顺序：分流必须早于 ``from .app import main``（漏了就变成普通桌宠启动）。

    锚在**真正那一行代码**上（行首缩进的 ``from .app import main as app_main``），
    不是"文件里任何地方出现过这串字"：上面几个 ``_run_*`` 的 docstring 里也会提到
    这个导入（2026-10-14 切片 5 的 ``_run_ocr_worker`` 就是这么写红的），照字符串
    取位置的话，一句注释就能让这条顺序断言失效。
    """
    source = (ROOT / "pet" / "__main__.py").read_text(encoding="utf-8")

    dispatch_at = source.index('"--uninstall-cleanup" in sys.argv')
    app_import_at = source.index("\n    from .app import main as app_main")
    assert dispatch_at < app_import_at
    assert "from .uninstall_cleanup import main as uninstall_cleanup_main" in source


def test_main_dispatches_uninstall_cleanup_to_shared_entry(monkeypatch):
    """``--uninstall-cleanup`` 派发给共享入口，并把它自己的退出码原样返回。"""
    import pet.__main__ as m

    calls = []
    legacy = []
    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(uninstall_cleanup, "main", lambda *a, **k: calls.append((a, k)) or 7)
    # 旧路径换成 spy：这条用例在任何实现下都不会碰真实自启/hooks/注册表
    monkeypatch.setattr(
        uninstall_cleanup,
        "run_uninstall_cleanup",
        lambda *a, **k: legacy.append((a, k)) or {"autostart": True},
    )

    assert m._main() == 7
    assert calls == [((), {})]
    assert legacy == []


def test_main_returns_shared_entry_failure_code(monkeypatch):
    """共享入口返回非零（有步骤失败）时，入口原样透传。"""
    import pet.__main__ as m

    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(uninstall_cleanup, "main", lambda *a, **k: 1)
    monkeypatch.setattr(uninstall_cleanup, "run_uninstall_cleanup", lambda *a, **k: {})

    assert m._main() != 0
