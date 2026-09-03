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


def test_supports_no_open_probes_help(monkeypatch):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="--no-open  Do not open browser", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    assert hl._supports_no_open(["dsh"]) is True

    def fake_run_missing(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="Usage: dsh web [options]", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_missing)
    hl._NO_OPEN_CACHE.clear()
    assert hl._supports_no_open(["dsh"]) is False


def test_supports_no_open_probe_failure_defaults_false(monkeypatch):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()

    def fake_run_fail(*args, **kwargs):
        raise TimeoutError("probe timeout")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_fail)
    assert hl._supports_no_open(["dsh"]) is False


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
