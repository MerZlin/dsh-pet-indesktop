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
    # 多开配置隔离：孵化出的桌宠使用独立配置文件/会话目录。
    # 实例 ID 必须对“独立母桌宠”也可区分——两个各自启动的母桌宠
    # 会同时孵出索引相同的孩子，仅用链路索引会撞车；带上母进程 PID
    # 保证共存期间唯一（PID 复用时复用旧配置=记住位置，属可接受行为）。
    # 注意必须覆盖从母进程继承来的 DSH_PET_INSTANCE，否则孩子与母桌宠同号。
    env["DSH_PET_INSTANCE"] = f"spawn{os.getpid()}x{child_index}"
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
