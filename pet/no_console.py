# -*- coding: utf-8 -*-
"""总闸：本进程（及其所有库）拉起的子进程**一律不弹控制台窗口**。

为什么要有这个（2026-10-13 机主两次报「弹窗」）：
- 桌宠是 windowed 进程；Windows 下 windowed 父进程起 console 子程序，**默认会弹一个黑窗**；
- 各处的 `subprocess.run/Popen` 一句句加 `CREATE_NO_WINDOW` 总会漏（实测漏在 webm 解码链、
  TTS 变调链），而且要依赖每个库自觉（`imageio_ffmpeg` 内部也自己处理，但形同虚设）；
- 机主对"又弹一个窗口"是**明文红线**，所以做成**进程级护栏**：导入即给 `subprocess.Popen`
  打上默认 `creationflags`，任何调用点（含第三方库内部）都覆盖到。

不碰的两种情况：
1. 已经显式要求 `DETACHED_PROCESS`（如拉起 dsh web 那种要脱离控制台的场景）——两者互斥；
2. 非 Windows。
"""

from __future__ import annotations

import subprocess
import sys

_INSTALLED = False


def install() -> bool:
    """打上护栏；重复调用无副作用。返回是否真的改了行为（Windows 上为 True）。"""
    global _INSTALLED
    if sys.platform != "win32" or _INSTALLED:
        return _INSTALLED
    original = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        flags = int(kwargs.get("creationflags") or 0)
        detached = getattr(subprocess, "DETACHED_PROCESS", 0)
        if not flags & detached:
            kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
        return original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched            # type: ignore[method-assign]
    _INSTALLED = True
    return True


install()
