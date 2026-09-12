# -*- coding: utf-8 -*-
"""issue #111 手工验收：向运行中的桌宠投递 Windows 会话结束消息并核对结果。

用途：不需要真的注销/关机就能验证「收到会话结束通知 → 停止派生 ffmpeg」的
行为（真机最终验收仍建议真的注销一次，见 docs/ISSUE-111-*.md §5）。

用法（Windows，桌宠已在运行）：

    python scripts/verify_session_end_shutdown.py            # 自动找桌宠窗口
    python scripts/verify_session_end_shutdown.py --hwnd 0x1234
    python scripts/verify_session_end_shutdown.py --message end_session

做法：
1. 定位桌宠顶层窗口（进程可执行名/窗口标题匹配）；
2. 记录当前 ffmpeg 子进程集合与日志文件大小；
3. ``PostMessageW(hwnd, WM_QUERYENDSESSION|WM_ENDSESSION, 0, 0)``
   —— 与系统关机时投递的消息同号同形（不 veto 关机语义，只是观测点触发）；
4. 等待若干秒后核对：
   - 日志出现「收到会话结束通知」与「会话结束：已停止全部 ffmpeg reader」；
   - 期间**没有新的** ffmpeg 子进程出现（这是 issue #111 的核心断言）。

注意：本脚本只对**已修复**的构建有意义；在修复前的构建上，第 4 步前一条
必然失败（进程对会话结束零感知，日志里不会出现那两行）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
MESSAGES = {"query_end_session": WM_QUERYENDSESSION, "end_session": WM_ENDSESSION}

WINDOW_TITLE_HINTS = ("dsh-pet", "dsd-pet", "桌宠", "宠物", "pet")
PROCESS_NAME_HINTS = ("dsh-pet",)


def _require_windows() -> None:
    if os.name != "nt":
        sys.exit("本脚本只在 Windows 上有意义（WM_QUERYENDSESSION 是 Windows 消息）")


def _pet_windows(only_exe: str = "") -> list:
    """枚举顶层窗口，返回候选桌宠窗口 [(hwnd, title, pid, exe)]。

    匹配分两档，避免误伤同类程序（本机可能装着别的桌宠应用，实测就踩过）：
    1. 进程可执行名命中 ``dsh-pet*``（本变体产物名，最可靠）；
    2. 否则退到标题提示词——此时脚本**不自动使用**，必须由 ``--hwnd`` 明确指定。
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    exact: list = []
    loose: list = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _process_name(kernel32, pid.value)
        if only_exe and only_exe.lower() not in exe.lower():
            return True
        if any(h in exe.lower() for h in PROCESS_NAME_HINTS):
            exact.append((int(hwnd), title, pid.value, exe))
        elif any(h in title.lower() for h in WINDOW_TITLE_HINTS):
            loose.append((int(hwnd), title, pid.value, exe))
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return exact or loose


def _process_name(kernel32, pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _ffmpeg_pids() -> set:
    """当前所有 ffmpeg 子进程 pid（经 PowerShell CIM 查询，避免额外依赖）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name like 'ffmpeg%'\").ProcessId"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",  # 中文 Windows 的 CIM 输出是 GBK/cp936
        ).stdout
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"  (无法枚举 ffmpeg 进程：{exc})")
        return set()
    return {int(x) for x in out.split() if x.strip().isdigit()}


def _pet_log_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "dsh-pet-standalone-webm-chat"


def main() -> int:
    _require_windows()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=lambda v: int(v, 0), default=None,
                        help="目标窗口句柄（默认自动定位桌宠窗口）")
    parser.add_argument("--exe", default="", help="限定进程可执行名（子串匹配）")
    parser.add_argument("--message", choices=sorted(MESSAGES), default="query_end_session")
    parser.add_argument("--settle", type=float, default=8.0,
                        help="投递后等待并观测的秒数（默认 8s）")
    args = parser.parse_args()

    hwnd = args.hwnd
    if hwnd is None:
        candidates = _pet_windows(args.exe)
        if not candidates:
            print("没找到候选窗口。请确认桌宠正在运行，或用 --hwnd/--exe 指定。")
            print("可用 Get-CimInstance Win32_Process 找到 pid，再用 Spy++ 定位窗口。")
            return 2
        print("候选窗口：")
        for h, title, pid, exe in candidates:
            print(f"  hwnd={h:#x} pid={pid} exe={exe!r} title={title!r}")
        # 只在「进程名明确是 dsh-pet」时才自动选用，避免把消息投给别的桌宠程序
        confident = [c for c in candidates
                     if any(h in c[3].lower() for h in PROCESS_NAME_HINTS)]
        if not confident:
            print("\n候选里没有进程名匹配 dsh-pet* 的窗口：请用 --hwnd 明确指定目标，")
            print("避免误把会话结束消息投给其它同类程序（实测踩过）。")
            return 2
        hwnd = confident[0][0]
        print(f"使用候选：hwnd={hwnd:#x}（进程名匹配 dsh-pet*）")

    log_dir = _pet_log_dir()
    logs_before = {p: p.stat().st_size for p in log_dir.glob("pet-*.log")} if log_dir.is_dir() else {}
    ffmpeg_before = _ffmpeg_pids()
    print(f"投递前：ffmpeg pids={sorted(ffmpeg_before)}，日志目录={log_dir}")

    message = MESSAGES[args.message]
    ok = ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), message, 0, 0)
    if not ok:
        print(f"PostMessageW 失败（err={ctypes.GetLastError()}）：句柄可能已失效")
        return 3
    print(f"已投递 {args.message}（{message:#06x}）到 hwnd={hwnd:#x}，观测 {args.settle:.0f}s…")

    deadline = time.monotonic() + max(0.0, args.settle)
    seen_new_ffmpeg: set = set()
    while time.monotonic() < deadline:
        seen_new_ffmpeg |= (_ffmpeg_pids() - ffmpeg_before)
        time.sleep(0.5)

    new_lines: list = []
    for path, size in logs_before.items():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(size)
                new_lines.extend(fh.read().splitlines())
        except OSError:
            pass

    armed = any("收到会话结束通知" in line for line in new_lines)
    froze = any("会话结束" in line and "ffmpeg reader" in line for line in new_lines)

    print("\n=== 结果 ===")
    print(f"日志「收到会话结束通知」      : {'OK' if armed else 'MISSING'}")
    print(f"日志「已停止全部 ffmpeg reader」: {'OK' if froze else 'MISSING'}")
    print(f"投递后新出现的 ffmpeg 进程   : {sorted(seen_new_ffmpeg) or '（无，符合预期）'}")
    if new_lines:
        print("新增日志：")
        for line in new_lines[-10:]:
            print(f"  {line}")

    passed = armed and not seen_new_ffmpeg
    print(f"\n判定：{'PASS' if passed else 'FAIL'}"
          f"{'（冻结日志行缺失，可能是日志目录/变体名不同）' if not froze else ''}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
