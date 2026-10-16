# -*- coding: utf-8 -*-
"""子进程控制台窗口总闸（``pet/no_console.py``）的回归。

机主对"又弹一个窗口"是明文红线：这条护栏必须在、必须生效、且不能破坏
`DETACHED_PROCESS`（故意脱离控制台）那种用法。

注意测试写法：**必须让护栏包住我们的记录器**（monkeypatch 掉 `Popen.__init__` 之后重新
`install()`），否则等于绕过总闸、测了个寂寞。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from pet import no_console


def _arm(monkeypatch, captured: dict) -> None:
    """把记录器装成 Popen.__init__，再让护栏包住它。"""

    def recorder(self, *args, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        raise RuntimeError("到此为止，不真的起进程")

    monkeypatch.setattr(subprocess.Popen, "__init__", recorder)
    monkeypatch.setattr(no_console, "_INSTALLED", False)
    assert no_console.install() is True


def test_guard_is_installed_on_windows() -> None:
    if sys.platform != "win32":
        pytest.skip("仅 Windows 有控制台窗口问题")
    assert no_console.install() is True
    assert no_console.install() is True          # 重复调用不重复包装


def test_spawned_process_gets_create_no_window(monkeypatch) -> None:
    """护栏生效后，即使调用方什么都不写，也会带上 CREATE_NO_WINDOW。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows")
    captured: dict = {}
    _arm(monkeypatch, captured)
    with pytest.raises(RuntimeError):
        subprocess.Popen(["cmd", "/c", "echo", "hi"])
    flags = int(captured.get("creationflags") or 0)
    assert flags & subprocess.CREATE_NO_WINDOW, (
        "子进程没带 CREATE_NO_WINDOW——就会弹控制台窗口（机主红线）"
    )


def test_detached_process_is_not_forced_hidden(monkeypatch) -> None:
    """故意脱离控制台的场景（DETACHED_PROCESS）不能被总闸改写（两者互斥）。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows")
    captured: dict = {}
    _arm(monkeypatch, captured)
    with pytest.raises(RuntimeError):
        subprocess.Popen(["cmd"], creationflags=subprocess.DETACHED_PROCESS)
    flags = int(captured.get("creationflags") or 0)
    assert not flags & subprocess.CREATE_NO_WINDOW, (
        "DETACHED_PROCESS 与 CREATE_NO_WINDOW 互斥，不能被总闸强行叠加"
    )
