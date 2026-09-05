# -*- coding: utf-8 -*-
"""一键清除子肥鱼：关闭所有小肥鱼进程并删除 slot 配置/会话/待办数据。

只操作 config 目录下“非当前进程”的 runtime 标记与 slot-* 数据文件；
主肥鱼（slot-0/config.json）不受影响。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    """跨平台探活：Windows 用 OpenProcess，其余用 kill(pid, 0)。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_pet_process(pid: int) -> None:
    """终止子肥鱼进程。Windows 使用 taskkill /T /F，POSIX 先 SIGTERM 再补 SIGKILL。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def clear_spawned_pets(config_dir: Path | str) -> dict:
    """关闭并清理所有小肥鱼（slot-N）数据。

    返回 {"killed_pids": [...], "deleted": [路径...]}。
    只处理非当前进程的 runtime 标记；slot-0 主肥鱼配置不会被删除。
    """
    root = Path(config_dir)
    killed_pids: list[int] = []
    deleted: list[str] = []

    # 1) 关闭仍在运行的子肥鱼进程，并清理 runtime 标记。
    try:
        markers = list(root.glob("runtime-*.json"))
    except OSError:
        markers = []
    for marker in markers:
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError):
            pid = 0
        if pid == os.getpid():
            continue
        if _pid_alive(pid):
            _terminate_pet_process(pid)
            killed_pids.append(pid)
        try:
            marker.unlink()
        except OSError:
            pass

    # 2) 删除 slot-N 的配置、会话与待办数据（主 config.json / sessions / todo_items.json 不碰）。
    for pattern in ("config-slot-*.json", "todo_items-slot-*.json"):
        try:
            matches = list(root.glob(pattern))
        except OSError:
            matches = []
        for path in matches:
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError:
                pass
    try:
        session_dirs = list(root.glob("sessions-slot-*"))
    except OSError:
        session_dirs = []
    for directory in session_dirs:
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            deleted.append(str(directory))

    return {"killed_pids": killed_pids, "deleted": deleted}
