# -*- coding: utf-8 -*-
"""真实 Windows 窗口诊断：枚举指定 PID 的顶层窗口，查位置/可见性/样式。"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def enum_windows(pid: int):
    results = []

    def cb(hwnd, lparam):
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid:
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            visible = user32.IsWindowVisible(hwnd)
            style = user32.GetWindowLongW(hwnd, -16)      # GWL_STYLE
            exstyle = user32.GetWindowLongW(hwnd, -20)    # GWL_EXSTYLE
            results.append({
                'hwnd': hwnd,
                'rect': (rect.left, rect.top, rect.right, rect.bottom),
                'visible': bool(visible),
                'exstyle': exstyle,
                'style': style,
            })
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results


def main() -> int:
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if not pid:
        print('用法: python tests/win_diag.py <pid>')
        return 1
    wins = enum_windows(pid)
    print(f'PID {pid} 的顶层窗口数: {len(wins)}')
    for w in wins:
        l, t, r, b = w['rect']
        print(f"  hwnd={w['hwnd']:#x} rect=({l},{t},{r},{b}) 尺寸={r-l}x{b-t} "
              f"visible={w['visible']} exstyle={w['exstyle']:#x}")
        # WS_EX_LAYERED=0x80000, WS_EX_TOOLWINDOW=0x80, WS_EX_TRANSPARENT=0x20
        if w['exstyle'] & 0x80000:
            print('    → 含 WS_EX_LAYERED（分层/透明窗口）')
        if w['exstyle'] & 0x80:
            print('    → 含 WS_EX_TOOLWINDOW')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
