# -*- coding: utf-8 -*-
"""issue #98 现场取证工具：复制粘贴失效时到底谁把剪贴板/输入挡住了。

用法（任选其一，故障复现期间保持运行）：
    python scripts/diagnose_clipboard.py            # 持续监视，按 Ctrl+C 结束
    python scripts/diagnose_clipboard.py --seconds 600

输出 CSV 到 <脚本目录>/clipboard-diag-<时间戳>.csv，同时在终端打印每次状态变化。
采样字段（全部为 Win32 真实状态，不做推测）：
    openable         OpenClipboard(NULL) 是否成功；False = 剪贴板被占用
                    （对应 CLIPBRD_E_CANT_OPEN：全局复制粘贴会同时失效）
    err              GetLastError（5=拒绝访问 / 0x800401D0 家族）
    owner            剪贴板所有者窗口
    owner_proc       所有者窗口所属进程名与 PID ← 真正"占着剪贴板"的元凶
    seq              剪贴板序号（复制成功一次会 +1；长时间不变说明复制根本没发生）
    fg / fg_proc     前台窗口及其进程 ← 判断按键被谁收走
    focus / focus_proc  该前台线程的键盘焦点窗口及其进程
    pets             正在运行的桌宠进程列表（判断是否多开/僵尸实例）

判读规则（拿到 CSV 后）：
1. openable=False 且 owner_proc 是桌宠 → 桌宠进程占着剪贴板（需按进程名定位泄漏点）；
2. openable=False 且 owner_proc 是别的程序 → 与桌宠无关，是那个程序的问题，
   桌宠只是"重启后顺手好了"；
3. openable 全程 True，但 fg_proc/focus_proc 变成桌宠 → 按键被桌宠收走：
   用户以为在编辑器里按 Ctrl+C，其实前台/焦点在桌宠上；
4. 全都正常 → 复制粘贴失效不是剪贴板被占，而是目标应用自身状态，
   与桌宠无关。

把 CSV 附到 issue #98 即可定案。
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

if sys.platform != 'win32':
    raise SystemExit('本工具仅支持 Windows（读取 Win32 剪贴板/前台窗口状态）。')

u32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.CloseClipboard.restype = wintypes.BOOL
u32.GetClipboardOwner.restype = wintypes.HWND
u32.GetClipboardSequenceNumber.restype = wintypes.DWORD
u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowThreadProcessId.restype = wintypes.DWORD
u32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PET_EXE_HINTS = ('dsh-pet', 'python', 'pythonw')

_proc_cache: dict[int, str] = {}


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
        ('hwndActive', wintypes.HWND), ('hwndFocus', wintypes.HWND),
        ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
        ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND),
        ('rcCaret', wintypes.RECT),
    ]


def _hwnd(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _proc_name(pid: int) -> str:
    if not pid:
        return ''
    cached = _proc_cache.get(pid)
    if cached is not None:
        return cached
    name = '?'
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = Path(buf.value).name
        finally:
            kernel32.CloseHandle(handle)
    _proc_cache[pid] = name
    return name


def _pid_of(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _title(hwnd: int) -> str:
    if not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def _focus_of_foreground(fg: int) -> int:
    if not fg:
        return 0
    tid = u32.GetWindowThreadProcessId(wintypes.HWND(fg), None)
    if not tid:
        return 0
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not u32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return 0
    return _hwnd(info.hwndFocus)


def _clipboard_state() -> tuple[bool, int, int]:
    ctypes.set_last_error(0)
    ok = bool(u32.OpenClipboard(None))
    err = ctypes.get_last_error() if not ok else 0
    if ok:
        u32.CloseClipboard()
    return ok, err, _hwnd(u32.GetClipboardOwner())


def _pet_pids() -> str:
    """当前进程名疑似桌宠的 PID 列表（用 tasklist 口径，避免引入依赖）。"""
    import subprocess

    try:
        out = subprocess.run(
            ['tasklist', '/fo', 'csv', '/nh'],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ''
    hits = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name, pid = parts[0].strip('"'), parts[1]
        low = name.lower()
        if any(hint in low for hint in PET_EXE_HINTS) and 'dsh' in low:
            hits.append(f'{name}:{pid}')
    return ','.join(hits)


def sample() -> dict:
    ok, err, owner = _clipboard_state()
    fg = _hwnd(u32.GetForegroundWindow())
    focus = _focus_of_foreground(fg)
    owner_pid, fg_pid, focus_pid = _pid_of(owner), _pid_of(fg), _pid_of(focus)
    return {
        'time': datetime.now().strftime('%H:%M:%S.%f')[:-3],
        'openable': int(ok),
        'err': err,
        'seq': int(u32.GetClipboardSequenceNumber()),
        'owner': f'{owner:#010x}',
        'owner_proc': f'{_proc_name(owner_pid)}({owner_pid})' if owner else '',
        'fg': f'{fg:#010x}',
        'fg_proc': f'{_proc_name(fg_pid)}({fg_pid})' if fg else '',
        'fg_title': _title(fg)[:60],
        'focus': f'{focus:#010x}',
        'focus_proc': f'{_proc_name(focus_pid)}({focus_pid})' if focus else '',
        'pets': _pet_pids(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='issue #98 剪贴板/焦点现场取证')
    ap.add_argument('--seconds', type=float, default=0.0,
                    help='运行时长（秒）；0 = 一直跑到 Ctrl+C')
    ap.add_argument('--interval', type=float, default=0.25, help='采样间隔（秒）')
    args = ap.parse_args()

    out = Path(__file__).resolve().parent / f'clipboard-diag-{int(time.time())}.csv'
    fields = list(sample().keys())
    print(f'取证输出: {out}')
    print('复现期间保持本脚本运行；复制粘贴一旦失效，请立刻看终端并记下时间点。\n')
    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    last_key = None
    with out.open('w', newline='', encoding='utf-8-sig') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        try:
            while True:
                row = sample()
                writer.writerow(row)
                fh.flush()
                key = (row['openable'], row['owner_proc'], row['fg_proc'],
                       row['focus_proc'], row['seq'], row['pets'])
                if key != last_key:
                    flag = 'CLIPBOARD-BLOCKED!' if not row['openable'] else ''
                    print(f"[{row['time']}] openable={row['openable']} err={row['err']} "
                          f"seq={row['seq']} owner={row['owner_proc'] or '-'} | "
                          f"fg={row['fg_proc'] or '-'} focus={row['focus_proc'] or '-'} "
                          f"| pets={row['pets'] or '-'} {flag}")
                    last_key = key
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(max(0.05, args.interval))
        except KeyboardInterrupt:
            print('\n已停止。')
    print(f'\n采样已写入: {out}')
    print('判读规则见本脚本 docstring（openable/owner_proc/fg_proc/focus_proc）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
