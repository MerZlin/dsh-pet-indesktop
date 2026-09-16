# -*- coding: utf-8 -*-
"""辅助进程：给 test_foreground_steal 起一个"用户应用"窗口并持有前台。

单独进程是必需的：同一进程的两个窗口共享前台队列，`GetForegroundWindow()`
无法区分"桌宠夺了前台"和"自己的另一个窗口仍是前台"。用法：
    python tests/helpers/foreground_holder.py <unused>
按 stdout 打印自己的 HWND，保持前台直到 stdin 关闭或超时。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

u32 = ctypes.windll.user32
u32.SetForegroundWindow.argtypes = [wintypes.HWND]
u32.SetForegroundWindow.restype = wintypes.BOOL

# 持有前台的时限（秒）：只有测试异常退出时才会走满，避免留下孤儿进程。
MAX_HOLD_SECONDS = 30.0


def _force_foreground(hwnd: int) -> bool:
    """把本窗口推上前台。

    Windows 的前台锁（SPI_GETFOREGROUNDLOCKTIMEOUT）会拒绝后台进程抢前台，
    `activateWindow()` 常常拿不到。SetForegroundWindow 对"刚生成窗口的进程"
    会被放行，这里反复重试直到成功或超时。
    """
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        u32.SetForegroundWindow(wintypes.HWND(hwnd))
        if u32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return u32.GetForegroundWindow() == hwnd


def main() -> int:
    app = QApplication.instance() or QApplication([])
    win = QWidget()
    win.setWindowTitle("PET-TEST-FOREGROUND-HOLDER")
    win.setGeometry(80, 80, 420, 180)
    edit = QLineEdit(win)
    edit.setGeometry(20, 20, 380, 36)
    edit.setText("hold-foreground")
    win.show()
    win.activateWindow()
    win.raise_()
    edit.setFocus()
    edit.selectAll()
    for _ in range(30):
        app.processEvents()

    hwnd = int(win.winId())
    got = _force_foreground(hwnd)
    # 让父进程拿到我们的 HWND 与"是否拿到前台"的结论
    print(f"{hwnd} {int(got)}", flush=True)

    deadline = time.monotonic() + MAX_HOLD_SECONDS
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
