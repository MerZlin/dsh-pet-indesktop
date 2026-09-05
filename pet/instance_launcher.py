# -*- coding: utf-8 -*-
"""启动独立的第二只桌宠进程。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# 模块加载时捕获真实 Popen 类型（测试会整体替换 subprocess.Popen，
# 登记判断须用真实类型；fake 返回的对象不入登记表）。
_POPEN_TYPE = subprocess.Popen

# 已孵化的子进程句柄登记：每次孵化前 poll() 回收已退出的进程，
# 避免 POSIX 上子进程退出后无人 waitpid 累积僵尸（Windows 上防句柄泄漏）。
_SPAWNED_CHILDREN: list[subprocess.Popen] = []


def _reap_children() -> None:
    for proc in list(_SPAWNED_CHILDREN):
        if proc.poll() is not None:
            _SPAWNED_CHILDREN.remove(proc)


def new_pet_command() -> list[str]:
    """返回与当前运行形态一致的桌宠启动命令。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "pet"]


def launch_new_pet(offset_index: int = 1):
    """脱离当前进程启动另一只桌宠，父桌宠退出后它仍继续运行。"""
    command = new_pet_command()
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    env = os.environ.copy()
    try:
        parent_index = max(0, int(env.get("DSH_PET_SPAWN_OFFSET_INDEX", "0")))
    except ValueError:
        parent_index = 0
    child_index = parent_index + max(1, int(offset_index))
    env["DSH_PET_SPAWN_OFFSET_INDEX"] = str(child_index)
    # 显式“生小肥鱼”标记：新进程即使复用旧的 slot-N 配置文件，
    # 也会在 Config 初始化时从主配置重新播种，避免“新鱼恢复默认设置”。
    env["DSH_PET_SPAWN_FRESH"] = "1"
    # 多开槽位机制：子进程自主竞争槽位（slot-1, slot-2, ...），
    # 不再生成 spawn{pid}x{n}，移除从父进程继承来的 DSH_PET_INSTANCE。
    env.pop("DSH_PET_INSTANCE", None)
    kwargs["env"] = env
    if getattr(sys, "frozen", False):
        kwargs["cwd"] = str(Path(sys.executable).resolve().parent)
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    _reap_children()
    proc = subprocess.Popen(command, **kwargs)
    if isinstance(proc, _POPEN_TYPE):
        _SPAWNED_CHILDREN.append(proc)
    return proc
