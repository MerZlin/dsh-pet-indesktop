# -*- coding: utf-8 -*-
"""DeepSeek Harness 一键启动器测试。"""
from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

from pet.harness_launcher import _find_launch_command, is_running


def test_harness_port_probe():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert is_running(port) is True
    finally:
        server.close()
    assert is_running(port) is False


def test_find_launch_command_resolves_web(monkeypatch):
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "_which", lambda name: "dsh" if name == "dsh" else None)

    monkeypatch.setattr(hl, "_supports_no_open", lambda base: True)
    command = hl._find_launch_command()
    assert command == ["dsh", "web", "--host", "127.0.0.1", "--port", "38080", "--no-open"]

    monkeypatch.setattr(hl, "_supports_no_open", lambda base: False)
    command = hl._find_launch_command()
    assert command == ["dsh", "web", "--host", "127.0.0.1", "--port", "38080"]
    assert "--no-open" not in command


def test_find_launch_command_fallback_without_dsh(monkeypatch):
    """PATH 上只有 node（无 dsh 命令）时，回退到 node + npm 全局包或 npx。"""
    from pet import harness_launcher as hl

    node = shutil.which("node")
    if not node:
        return  # 本机没有 node，跳过该场景
    monkeypatch.setattr(hl, "_supports_no_open", lambda base: False)
    monkeypatch.setenv("PATH", str(Path(node).parent))
    command = hl._find_launch_command()
    assert command is not None and "web" in command
    allowed = ("node", "node.exe", "npx", "npx.cmd")
    if os.name == "nt":
        # Windows 上 npm 全局 dsh 是 .cmd shim，启动器用 cmd.exe 包装执行
        allowed = allowed + ("cmd.exe",)
    assert os.path.basename(command[0]).lower() in allowed


def test_supports_no_open_probes_help(monkeypatch, tmp_path):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: tmp_path / "nope.json")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="--no-open  Do not open browser", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    assert hl._supports_no_open(["dsh"]) is True

    def fake_run_missing(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="Usage: dsh web [options]", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_missing)
    hl._NO_OPEN_CACHE.clear()
    assert hl._supports_no_open(["dsh"]) is False


def test_supports_no_open_probe_failure_defaults_false(monkeypatch, tmp_path):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: tmp_path / "nope.json")

    def fake_run_fail(*args, **kwargs):
        raise TimeoutError("probe timeout")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_fail)
    assert hl._supports_no_open(["dsh"]) is False


def test_supports_no_open_disk_cache(monkeypatch, tmp_path):
    """落盘缓存：版本匹配时直接用缓存零探测；版本变了才重新慢探测。"""
    import json as _json
    from pet import harness_launcher as hl

    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: cache_file)
    monkeypatch.setattr(hl, "_dsh_version", lambda cmd: "0.1.1-rc.2")
    hl._NO_OPEN_CACHE.clear()

    def _explode(*args, **kwargs):
        raise AssertionError("缓存命中时不应再跑慢探测")

    # 缓存命中：probe 爆炸也不应被调用
    cache_file.write_text(_json.dumps(
        {"cmd": ["dsh"], "version": "0.1.1-rc.2", "no_open": True}), encoding="utf-8")
    monkeypatch.setattr(hl, "_probe_no_open", _explode)
    assert hl._supports_no_open(["dsh"]) is True

    # 版本变了：缓存失效，回落到慢探测
    monkeypatch.setattr(hl, "_dsh_version", lambda cmd: "0.1.2")
    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_no_open", lambda cmd: (False, True))
    assert hl._supports_no_open(["dsh"]) is False

    # 探测失败（probe_ok=False）不写缓存，避免把超时误判固化
    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_no_open", lambda cmd: (False, False))
    assert hl._supports_no_open(["dsh"]) is False
    # 缓存应保持第二段写入的 0.1.2 版本内容，未被失败探测覆盖
    assert _json.loads(cache_file.read_text(encoding="utf-8"))["version"] == "0.1.2"

    # 探测失败 + 版本不匹配的旧缓存 → 兜底沿用旧答案（开机超时不再误判）
    cache_file.write_text(_json.dumps(
        {"cmd": ["dsh"], "version": "9.9.9", "no_open": True}), encoding="utf-8")
    hl._NO_OPEN_CACHE.clear()
    assert hl._supports_no_open(["dsh"]) is True


def test_launch_harness_reuses_existing_instance_on_alt_port(monkeypatch):
    """已有 dsh web 跑在官方默认 3080 时，直接复用打开，不再新起 38080。"""
    from pet import harness_launcher as hl

    opened = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    # 38080 无监听，3080 有
    monkeypatch.setattr(hl, "is_running", lambda port=None: int(port or 38080) == 3080)

    def _no_spawn(command):  # 不应走到启动分支
        raise AssertionError("已有实例运行时不应再 spawn")

    monkeypatch.setattr(hl, "_spawn", _no_spawn)
    status, url = hl.launch_harness()
    assert status == "already"
    assert url == "http://127.0.0.1:3080"
    assert opened == ["http://127.0.0.1:3080"]


def test_launch_harness_prefers_configured_port(monkeypatch):
    """配置端口已有实例时优先复用它，不再探测 3080。"""
    from pet import harness_launcher as hl

    opened = []
    probed = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))

    def _probe(port=None):
        probed.append(int(port or 38080))
        return True  # 第一个候选（配置端口）即有监听

    monkeypatch.setattr(hl, "is_running", _probe)
    status, url = hl.launch_harness(port=38080)
    assert status == "already"
    assert probed == [38080]
    assert opened == ["http://127.0.0.1:38080"]


def test_launch_harness_no_browser_when_autostart(monkeypatch):
    """open_browser=False（随桌宠自启动）：只起服务，任何分支都不开浏览器。"""
    from pet import harness_launcher as hl

    opened = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))

    # 分支1：已有实例 → 直接返回，不开浏览器
    monkeypatch.setattr(hl, "is_running", lambda port=None: True)
    status, url = hl.launch_harness(open_browser=False)
    assert status == "already"
    assert opened == []

    # 分支2：新起（带 --no-open）→ 不起等待线程、不开浏览器
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: None)
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", "38080", "--no-open"],
    )
    threads = []

    def fake_thread(target=None, daemon=None, **kwargs):
        threads.append(target)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(hl.threading, "Thread", fake_thread)
    status, url = hl.launch_harness(open_browser=False)
    assert status == "started"
    assert opened == []
    assert threads == [], "open_browser=False 不应启动等待开浏览器的线程"


def test_harness_autostart_hook_gates(monkeypatch):
    """AppShell._maybe_autostart_harness：配置关/无 Chat 不触发；已有实例不重复拉起。"""
    from pet import app as app_mod
    from pet import harness_launcher as hl

    spawned = []
    monkeypatch.setattr(
        app_mod.threading, "Thread",
        lambda target=None, daemon=None, name=None: SimpleNamespace(start=lambda: spawned.append(target)),
    )

    def _make(enable_chat, flag):
        return SimpleNamespace(
            enable_chat=enable_chat,
            config=SimpleNamespace(get=lambda k, d=None: flag if k == "harness_autostart" else d),
        )

    app_mod.AppShell._maybe_autostart_harness(_make(True, False))
    app_mod.AppShell._maybe_autostart_harness(_make(False, True))
    assert spawned == []

    app_mod.AppShell._maybe_autostart_harness(_make(True, True))
    assert len(spawned) == 1

    launched = []
    monkeypatch.setattr(hl, "is_running", lambda port=None: True)
    monkeypatch.setattr(hl, "launch_harness", lambda **kw: launched.append(kw))
    spawned[0]()
    assert launched == [], "已有 dsh 实例在跑时不得重复拉起"


def test_launch_harness_browser_ownership(monkeypatch):
    from pet import harness_launcher as hl

    opened = []
    threads = []

    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: None)

    def fake_thread(target=None, daemon=None, **kwargs):
        started = []

        def start():
            started.append((target, daemon))

        t = SimpleNamespace(start=start)
        threads.append((t, started))
        return t

    monkeypatch.setattr(hl.threading, "Thread", fake_thread)

    # 不带 --no-open：dsh 自己开浏览器，桌宠不重复打开
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", "38080"],
    )
    status, url = hl.launch_harness()
    assert status == "started"
    assert opened == []
    assert threads == []

    # 带 --no-open：桌宠等待就绪后打开浏览器
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", "38080", "--no-open"],
    )
    status, url = hl.launch_harness()
    assert status == "started"
    assert threads, "带 --no-open 时应启动等待线程"
    assert opened == []


def test_spawn_injects_augmented_path(monkeypatch):
    """子进程必须继承增强 PATH：macOS Finder 启动的 .app 原 PATH 极简，
    dsh/npx 的 shebang（/usr/bin/env node）依赖子进程环境找 node。"""
    import subprocess

    from pet import harness_launcher as hl

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(hl.subprocess, "Popen", fake_popen)
    hl._spawn(["dsh", "web"])
    env = captured["kwargs"]["env"]
    assert env["PATH"] == hl._augmented_path()
    # 增强 PATH 是完整 PATH 的超集（前缀 + 原 PATH）
    original = hl._augmented_path()
    assert env["PATH"] == original


def test_node_runtime_augments_finder_path_with_homebrew(monkeypatch):
    """Issue #67：macOS Finder 的极简 PATH 仍能覆盖 Homebrew bin。"""
    if os.name == "nt":
        return
    from pet import node_runtime

    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setattr(
        node_runtime.Path,
        "is_dir",
        lambda path: str(path) == "/opt/homebrew/bin",
    )
    captured = {}

    def fake_which(name, path=None):
        captured["name"] = name
        captured["path"] = path
        return "/opt/homebrew/bin/node"

    monkeypatch.setattr(node_runtime.shutil, "which", fake_which)

    assert node_runtime.which("node") == "/opt/homebrew/bin/node"
    assert captured == {
        "name": "node",
        "path": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }


def test_npm_root_probe_skipped_when_npm_missing(monkeypatch):
    """PATH 上没有 npm 时不应执行 npm root -g（避免菜单点击卡 15 秒）。"""
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("npm not found")

    monkeypatch.setattr(hl, "_which", lambda name: None)
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls == [], "npm 不存在时不应探测 npm root -g"
    assert any(r.name == "node_modules" for r in roots)  # 静态候选仍保留


def test_npm_root_probe_runs_when_npm_present(monkeypatch):
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        result = SimpleNamespace(returncode=0, stdout="/fake/global/node_modules\n")
        return result

    monkeypatch.setattr(hl, "_which", lambda name: "/fake/npm" if name == "npm" else None)
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls, "npm 存在时应执行 npm root -g"
    assert Path("/fake/global/node_modules") in roots
