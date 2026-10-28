"""没有内嵌内核时的外部打开路径：Edge 应用窗口优先，其次系统默认浏览器。

整份模块只用标准库（不导入 Qt），因此测试与运行时都能在无显示环境里安全导入。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000


def edge_path() -> str:
    candidates: list[Path] = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    candidates.extend(
        (
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        )
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return shutil.which("msedge") or ""


def open_edge_app(url: str) -> bool:
    """用 Edge 的 `--app` 模式开一个无地址栏窗口，复用用户已有配置与登录态。"""
    exe = edge_path()
    if not exe or not url:
        return False
    try:
        subprocess.Popen(
            [exe, f"--app={url}", "--new-window"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    return True


def open_system(url: str) -> bool:
    """系统默认浏览器：纯标准库（Windows `os.startfile`，其它平台 `webbrowser`）。"""
    if not url:
        return False
    try:
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 - 交给系统 shell 打开默认浏览器
            return True
        import webbrowser

        return bool(webbrowser.open(url))
    except OSError:
        return False


def open_external(url: str) -> str:
    """返回实际使用的通道："edge" / "system" / ""（都失败）。"""
    if open_edge_app(url):
        return "edge"
    if open_system(url):
        return "system"
    return ""


def channel_label() -> str:
    if edge_path():
        return "Edge 应用窗口"
    return "系统默认浏览器"
