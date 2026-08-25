# -*- coding: utf-8 -*-
"""DeepSeek Harness 一键启动器测试。"""

import os
import shutil
import socket
from pathlib import Path

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


def test_find_launch_command_resolves_web():
    command = _find_launch_command()
    if command is not None:
        assert "web" in command
        # 端口必须显式传给 dsh（默认 38080，避开 winnat 保留段 3080）
        assert command[command.index("web"):] == ["web", "--host", "127.0.0.1", "--port", "38080"]


def test_find_launch_command_fallback_without_dsh(monkeypatch):
    """PATH 上只有 node（无 dsh 命令）时，回退到 node + npm 全局包或 npx。"""
    from pet import harness_launcher as hl

    node = shutil.which("node")
    if not node:
        return  # 本机没有 node，跳过该场景
    monkeypatch.setenv("PATH", str(Path(node).parent))
    command = hl._find_launch_command()
    assert command is not None and "web" in command
    assert os.path.basename(command[0]).lower() in ("node", "node.exe", "npx", "npx.cmd")


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


def test_npm_root_probe_skipped_when_npm_missing(monkeypatch):
    """PATH 上没有 npm 时不应执行 npm root -g（避免菜单点击卡 15 秒）。"""
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("npm not found")

    monkeypatch.setattr(hl.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls == [], "npm 不存在时不应探测 npm root -g"
    assert any(r.name == "node_modules" for r in roots)  # 静态候选仍保留


def test_npm_root_probe_runs_when_npm_present(monkeypatch):
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        import types

        result = types.SimpleNamespace(returncode=0, stdout="/fake/global/node_modules\n")
        return result

    monkeypatch.setattr(
        hl.shutil, "which", lambda name, path=None: "/fake/npm" if name == "npm" else None
    )
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls, "npm 存在时应执行 npm root -g"
    assert Path("/fake/global/node_modules") in roots
