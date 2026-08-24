# -*- coding: utf-8 -*-
"""一键启动 DeepSeek Harness（dsh web，默认端口 3080）。

启动命令解析按可靠性级联（适配不同安装方式/不同 PATH 的电脑）：

1. PATH 上的 `dsh`（npm/pnpm/yarn/bun 全局安装、或用户自建软链）；
2. `node` + npm 全局包内的 `@deepseek-ai/dsh/lib/bin.js`；
3. 官方推荐的 `npx --yes @deepseek-ai/dsh web`（未安装时自动拉取，
   见 https://github.com/deepseek-ai/DeepSeek-Harness 运行文档）。

macOS：Finder 启动的 .app 环境 PATH 极简，本模块会额外探测 Homebrew、
nvm、volta、bun、pnpm 等常见安装目录后回退 npx；需要机器装有 Node.js。

行为：探测端口 —— 已在运行则直接打开浏览器；未运行则后台拉起
（Windows 隐藏窗口脱离进程 / POSIX 新会话），就绪后自动打开浏览器。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

DEFAULT_PORT = 3080
HARNESS_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
# npx 首次拉取 @deepseek-ai/dsh 可能较慢，预留 90 秒就绪窗口
_READY_TIMEOUT_SECONDS = 90.0

# macOS/Linux 上 Finder/launchd 启动的应用 PATH 很精简，
# 这里补充常见包管理器 bin 目录（存在才加入，避免无效探测）。
_POSIX_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "~/.npm-global/bin",
    "~/.local/bin",
    "~/.volta/bin",
    "~/.bun/bin",
    "~/.yarn/bin",
    "~/Library/pnpm",
    "~/.local/share/pnpm",
)

_POSIX_NODE_MODULES = (
    "~/.local/lib/node_modules",
    "~/.npm-global/lib/node_modules",
    "/usr/local/lib/node_modules",
    "/opt/homebrew/lib/node_modules",
    "/usr/lib/node_modules",
)


def is_running(port: int = DEFAULT_PORT) -> bool:
    """探测 127.0.0.1:port 是否有服务监听。"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _augmented_path() -> str:
    """在原 PATH 前拼接常见 bin 目录（Windows 直接返回原 PATH）。"""
    if os.name == "nt":
        return os.environ.get("PATH", "")
    extra: list[str] = []
    for directory in _POSIX_BIN_DIRS:
        path = Path(directory).expanduser()
        if path.is_dir():
            extra.append(str(path))
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        extra.extend(str(p) for p in sorted(nvm.glob("*/bin")) if p.is_dir())
    return os.pathsep.join([*extra, os.environ.get("PATH", "")])


def _which(name: str) -> str | None:
    return shutil.which(name, path=_augmented_path())


def _wrap_cmd(command: list[str]) -> list[str]:
    """Windows 上 .cmd/.bat shim 必须经 cmd 启动并立即返回（start /b）。"""
    if os.name == "nt" and command[0].lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", "start", "/b", "", *command]
    return command


def _npm_global_roots() -> list[Path]:
    """候选的 npm 全局 node_modules 根目录。"""
    roots: list[Path] = []
    if os.name == "nt":
        roots.append(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules")
    else:
        roots.extend(Path(directory).expanduser() for directory in _POSIX_NODE_MODULES)
    # 只在 PATH（增强后）确实存在 npm 时才探测，避免菜单里点击卡住 15 秒
    if shutil.which("npm", path=_augmented_path()) is not None:
        try:
            result = subprocess.run(
                ["npm", "root", "-g"], capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                roots.append(Path(result.stdout.strip()))
        except Exception:
            pass
    return roots


def _find_launch_command() -> list[str] | None:
    """级联解析 dsh 启动命令；找不到返回 None。"""
    # 1) PATH 上的 dsh（各包管理器全局安装）
    dsh = _which("dsh")
    if dsh:
        return _wrap_cmd([dsh, "web"])

    # 2) node + npm 全局包内的 bin.js
    node = _which("node")
    for root in _npm_global_roots():
        bin_js = root / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        if bin_js.is_file():
            if node:
                return [node, str(bin_js), "web"]
            # POSIX：bin.js 有 shebang 可直跑；Windows 上必须经 node
            if os.name != "nt":
                return [str(bin_js), "web"]

    # 3) 官方推荐：npx --yes @deepseek-ai/dsh web（首次会自动拉取）
    npx = _which("npx")
    if npx:
        return _wrap_cmd([npx, "--yes", "@deepseek-ai/dsh", "web"])
    if node:
        npx_side = Path(node).with_name("npx")  # npx 随 Node 一起分发
        if npx_side.is_file():
            return _wrap_cmd([str(npx_side), "--yes", "@deepseek-ai/dsh", "web"])
    return None


def _spawn(command: list[str]) -> None:
    """后台拉起进程：Windows 隐藏窗口并脱离；POSIX 新会话脱离终端。

    macOS 上 Finder 启动的 .app 环境 PATH 极简：dsh/npx 是带 shebang
    （/usr/bin/env node）的 shell 脚本，执行时用的是**子进程环境**的 PATH，
    而非 _which 用的增强 PATH——不注入增强 PATH 会静默失败
    （env: node: No such file or directory，45 秒后无反应）。
    """
    kwargs: dict = {
        "cwd": str(Path.home()),  # dsh 以调用目录为默认工作区，用家目录保持中性
        "close_fds": True,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {**os.environ, "PATH": _augmented_path()},
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def launch_harness(port: int = DEFAULT_PORT) -> tuple[str, str]:
    """启动 harness 并确保浏览器被打开。

    返回 (status, url)：
    - already   已在运行，已打开浏览器
    - started   已后台启动，就绪后自动打开浏览器
    - not-found 未找到 dsh 命令
    - error     启动异常（info 为异常信息）
    """
    if is_running(port):
        webbrowser.open(HARNESS_URL)
        return "already", HARNESS_URL
    command = _find_launch_command()
    if command is None:
        return "not-found", HARNESS_URL
    try:
        _spawn(command)
    except OSError as exc:
        return "error", str(exc)

    def _wait_and_open() -> None:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if is_running(port):
                webbrowser.open(HARNESS_URL)
                return
            time.sleep(0.5)

    threading.Thread(target=_wait_and_open, daemon=True).start()
    return "started", HARNESS_URL


def launch_harness_gui(parent=None) -> None:
    """GUI 菜单入口：静默启动/打开浏览器，仅在失败时弹窗提示。

    弹窗延迟到菜单关闭后再显示：macOS 原生菜单跟踪会话中弹模态框
    会被 AppKit 抑制（与设置对话框首次点击无反应同源）。
    """
    status, info = launch_harness()
    if status in ("already", "started"):
        return

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    def _show() -> None:
        if status == "not-found":
            QMessageBox.warning(
                parent,
                "启动 DeepSeek Harness",
                "未找到 dsh 命令。请先安装 Node.js 后执行：\n"
                "npm install -g @deepseek-ai/dsh\n"
                "或直接使用：npx @deepseek-ai/dsh web",
            )
        elif status == "error":
            QMessageBox.critical(parent, "启动 DeepSeek Harness", f"启动失败：{info}")

    QTimer.singleShot(0, _show)
