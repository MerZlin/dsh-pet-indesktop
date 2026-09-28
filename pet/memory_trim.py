# -*- coding: utf-8 -*-
"""温和回收内存：把别的进程已经用不着的工作集还给系统。

**本模块不 import Qt**：进程挑选、统计聚合与文案都在这里；OS 调用统一走
``_Win32Backend``（可注入替身），因此选择/聚合逻辑能离线单测
（见 ``tests/test_memory_trim.py``）。展示与调度在 ``pet/memory_trim_service.py``。

口径（对齐参考实现 deepseek-dafeiyu-pet 的实测结论）：

- 只对"能碰到的"进程调 ``EmptyWorkingSet``——**不提权、不弹 UAC**（安装包默认就是这个身份）；
- 比 ``min_bytes`` 还小的进程不动（收了也省不下什么）；
- 系统关键进程名单不动（动了没收益，还可能让服务顿一下）；
- 跳过自己与前台进程（前台被收了要重新读盘，用户会觉得卡）；
- 统计口径固定为 trimmed / skipped_small / skipped_named / denied / failed / freed /
  avail_before / avail_after / elapsed_ms，气泡只报"几个程序腾出多少"。

⚠ 与 ``pet/runtime_cleanup.py`` 区分：那个清 PyInstaller 的 ``_MEI*`` 临时目录，
这个是**全局工作集回收**，两件事不要混在一个模块里。
"""
from __future__ import annotations

import ctypes
import os
import time

# 比这还小的进程不动（MB）
MEM_TRIM_MIN_MB = 20
# 系统关键进程：动了没收益，还可能让服务顿一下
SKIP_EXES = frozenset({
    "system", "idle", "registry", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "winlogon.exe", "svchost.exe", "dwm.exe", "audiodg.exe",
})


def human_mb(value) -> str:
    """字节 → 人话（``2.6 GB`` / ``251 MB``）。"""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "0 MB"
    if size <= 0:
        return "0 MB"
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.0f} MB"
    return f"{size / 1024:.0f} KB"


def clean_min_mb(value, default: int = MEM_TRIM_MIN_MB) -> int:
    """最小进程尺寸（MB）清洗：非法值回落默认，夹到 0~1024。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(0, min(1024, size))


def select_targets(processes, *, skip_pids=(), skip_exes=(), min_bytes=0):
    """从 ``[(pid, exe, working_set_bytes)]`` 里挑出可回收的目标。

    返回 ``(targets, skipped_small, skipped_named)``；targets 是 ``[(pid, before_bytes)]``。
    纯函数：不碰 OS，便于验证"太小不动 / 名单不动 / 自己不动"这三条口径。
    """
    skip_pids = {int(pid) for pid in skip_pids}
    skip_exes = {str(name).strip().lower() for name in skip_exes if str(name).strip()}
    skip_exes |= SKIP_EXES
    targets: list[tuple[int, int]] = []
    skipped_small = 0
    skipped_named = 0
    for pid, exe, working_set in processes:
        try:
            pid = int(pid)
            working_set = int(working_set)
        except (TypeError, ValueError):
            continue
        if pid in skip_pids:
            continue
        name = str(exe or "").strip().lower()
        if name and name in skip_exes:
            skipped_named += 1
            continue
        if working_set < int(min_bytes):
            skipped_small += 1
            continue
        targets.append((pid, working_set))
    return targets, skipped_small, skipped_named


def summarize(result: dict) -> str:
    """把统计字典说成一句人话（空手而归也给一句，不装作干了活）。"""
    if not result or result.get("error"):
        return "没收拾成，等会儿再试试"
    trimmed = int(result.get("trimmed") or 0)
    if not trimmed:
        return "这会儿干干净净的，没什么好收拾"
    freed = int(result.get("freed") or 0)
    line = f"收拾好啦：{trimmed} 个程序腾出 {human_mb(freed)}"
    extra = int(result.get("avail_after") or 0) - int(result.get("avail_before") or 0)
    if extra > 16 * 1024 * 1024:
        line += f"，可用内存多出 {human_mb(extra)}"
    return line


class _Win32Backend:
    """真实 OS 出口：所有 ctypes 细节集中在这里（**argtypes 必须声明**，
    否则 64 位下句柄会被当 32 位截断——参考实现里踩过的坑）。"""

    def __init__(self) -> None:
        from ctypes import wintypes

        self._wintypes = wintypes
        self.kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.psapi.EnumProcesses.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        self.psapi.EnumProcesses.restype = wintypes.BOOL
        self.psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(self._pmc_type()), wintypes.DWORD,
        ]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        self.psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        self.psapi.EmptyWorkingSet.restype = wintypes.BOOL

    def _pmc_type(self):
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        return _PMC

    def list_processes(self):
        from ctypes import wintypes

        arr = (wintypes.DWORD * 4096)()
        needed = wintypes.DWORD()
        if not self.psapi.EnumProcesses(
                ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        out = []
        for index in range(needed.value // ctypes.sizeof(wintypes.DWORD)):
            pid = int(arr[index])
            if not pid:
                continue
            exe = self.exe_of(pid)
            out.append((pid, exe, self.memory_info(pid) or 0))
        return out

    def exe_of(self, pid: int) -> str:
        from ctypes import wintypes

        handle = self.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value or "").lower()
        finally:
            self.kernel32.CloseHandle(handle)
        return ""

    def memory_info(self, pid: int):
        pmc = self._pmc_type()()
        pmc.cb = ctypes.sizeof(pmc)
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            if self.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
        finally:
            self.kernel32.CloseHandle(handle)
        return None

    def open_process(self, pid: int):
        # QUERY_INFORMATION | SET_QUOTA：只有这两个权限才允许 EmptyWorkingSet
        return self.kernel32.OpenProcess(0x0400 | 0x0100, False, pid)

    def empty_working_set(self, handle) -> bool:
        return bool(self.psapi.EmptyWorkingSet(handle))

    def close_handle(self, handle) -> None:
        self.kernel32.CloseHandle(handle)

    def available_memory(self) -> int:
        from ctypes import wintypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if self.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
        return 0


def trim_working_sets(*, skip_pids=(), skip_exes=(), min_bytes=None, backend=None) -> dict:
    """温和回收一趟：返回统计字典（失败不抛，带 ``error`` 字段）。

    ``backend`` 可注入替身（测试用）；缺省走 Windows 真实实现。非 Windows 直接返回
    ``error``，调用方照常说"没收拾成"即可。
    """
    out = {
        "trimmed": 0, "skipped_small": 0, "skipped_named": 0, "denied": 0, "failed": 0,
        "freed": 0, "avail_before": 0, "avail_after": 0, "elapsed_ms": 0,
    }
    started = time.perf_counter()
    if os.name != "nt":
        out["error"] = "仅支持 Windows"
        return out
    floor = (MEM_TRIM_MIN_MB * 1024 * 1024) if min_bytes is None else int(min_bytes)
    try:
        backend = backend or _Win32Backend()
    except Exception as exc:      # 取 API 就失败：如实报告，不假装收拾过
        out["error"] = str(exc)
        return out
    try:
        out["avail_before"] = backend.available_memory()
        targets, skipped_small, skipped_named = select_targets(
            backend.list_processes(), skip_pids=skip_pids, skip_exes=skip_exes, min_bytes=floor,
        )
        out["skipped_small"] = skipped_small
        out["skipped_named"] = skipped_named
        for pid, before in targets:
            handle = backend.open_process(pid)
            if not handle:
                out["denied"] += 1
                continue
            try:
                ok = backend.empty_working_set(handle)
            finally:
                backend.close_handle(handle)
            if not ok:
                out["failed"] += 1
                continue
            out["trimmed"] += 1
            after = backend.memory_info(pid)
            if after is not None and before:
                out["freed"] += max(0, int(before) - int(after))
        out["avail_after"] = backend.available_memory()
    except Exception as exc:
        out["error"] = str(exc)
    out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return out
