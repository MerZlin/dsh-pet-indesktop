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
        assert command[-1] == "web"


def test_find_launch_command_fallback_without_dsh(monkeypatch):
    """PATH 上只有 node（无 dsh 命令）时，回退到 node + npm 全局包或 npx。"""
    from pet import harness_launcher as hl

    node = shutil.which("node")
    if not node:
        return  # 本机没有 node，跳过该场景
    monkeypatch.setenv("PATH", str(Path(node).parent))
    command = hl._find_launch_command()
    assert command is not None and command[-1] == "web"
    assert os.path.basename(command[0]).lower() in ("node", "node.exe", "npx", "npx.cmd")
