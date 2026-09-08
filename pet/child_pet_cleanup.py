# -*- coding: utf-8 -*-
"""一键退出子肥鱼：关闭所有小肥鱼进程并清理 runtime 标记。

只退出子肥鱼进程/窗口（相当于一键退出其他所有子肥鱼），**不删除**它们的
slot 配置、会话与待办数据——子肥鱼的设置（含 user_customized 占位）全部
保留，下次生成时按占位语义恢复。主肥鱼（slot-0/config.json）不受影响。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

from . import slot_manager as slot_manager_mod


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


def _wait_pid_dead(pid: int, timeout: float = 2.0) -> bool:
    """杀进程后确认其真的退出（taskkill 返回 ≠ 目标进程已消失）。有界轮询。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def clear_spawned_pets(config_dir: Path | str) -> dict:
    """关闭所有小肥鱼（slot-N）进程并清理其 runtime 标记。

    只退出进程，**不删除** slot 配置/会话/待办数据（子肥鱼设置保留，
    下次生成按占位语义恢复）。只处理非当前进程的 runtime 标记；
    slot-0 主肥鱼不受影响。
    返回 {"killed_pids": [...], "failed_pids": [...]}。

    批 G：杀进程后必须确认进程真的死掉才删标记——旧实现「杀失败静默吞掉 +
    无条件删标记」，子进程存活且痕迹清零（实机复现：第二只子肥鱼幸存），
    且全程零日志无法排查。杀失败的 pid 保留标记并记入 failed_pids，
    供下次重试/结果框呈报。
    """
    root = Path(config_dir)
    killed_pids: list[int] = []
    failed_pids: list[int] = []

    # 关闭仍在运行的子肥鱼进程，并清理 runtime 标记。
    # 同时认旧名 runtime-*.json 与批5.2 版本化新名 pet-runtime-v2-*.json
    #（多进程模式只写 v2 名，旧 glob 匹配不到 → 子进程杀不掉）。
    markers = slot_manager_mod.list_runtime_marker_files(root)
    if markers:
        logging.info(
            "退出子肥鱼：发现 %d 个 runtime 标记: %s",
            len(markers), [m.name for m in markers])
    for marker in markers:
        # 兜底防御：v2 标记名带 slot 编号，slot-0 是主肥鱼，永不杀（即便调用方
        # 是子肥鱼进程——其 pid==os.getpid() 只跳过自己，主鱼标记会被误杀）。
        if marker.name.startswith(slot_manager_mod._RUNTIME_V2_PREFIX):
            if marker.name.rsplit("-slot-", 1)[-1].removesuffix(".json") == "0":
                continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError):
            pid = 0
        if pid == os.getpid():
            continue
        if pid > 0 and _pid_alive(pid):
            logging.info("退出子肥鱼：结束子进程 pid=%d (%s)", pid, marker.name)
            dead = False
            for attempt in (1, 2):
                _terminate_pet_process(pid)
                if _wait_pid_dead(pid):
                    dead = True
                    break
                logging.warning(
                    "退出子肥鱼：第 %d 次结束 pid=%d 后进程仍存活", attempt, pid)
            if dead:
                killed_pids.append(pid)
            else:
                failed_pids.append(pid)
                logging.warning(
                    "退出子肥鱼：pid=%d 未能退出，保留标记供下次重试", pid)
                continue  # 进程仍活：保留标记，不毁灭痕迹
        try:
            marker.unlink()
        except OSError:
            pass

    return {"killed_pids": killed_pids, "failed_pids": failed_pids}
