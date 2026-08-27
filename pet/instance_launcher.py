# -*- coding: utf-8 -*-
"""启动独立的第二只桌宠进程。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
    env["DSH_PET_SPAWN_OFFSET_INDEX"] = str(parent_index + max(1, int(offset_index)))
    kwargs["env"] = env
    if getattr(sys, "frozen", False):
        kwargs["cwd"] = str(Path(sys.executable).resolve().parent)
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)
