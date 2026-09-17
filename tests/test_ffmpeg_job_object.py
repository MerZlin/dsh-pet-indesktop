# -*- coding: utf-8 -*-
"""孤儿 ffmpeg 回归：Windows Job Object（KILL_ON_JOB_CLOSE）验收。

实机定案（2026-09-17）：桌宠进程被 ``Stop-Process`` / 任务管理器强杀
（``TerminateProcess``，父进程任何清理代码都不执行）后，imageio-ffmpeg 拉起的
ffmpeg 子进程不退出——本机实测残留一个 62MB 的 ``ffmpeg-win-x86_64``；每次强杀
/ 崩溃留一个。

修法：在 imageio-ffmpeg 的 Popen 唯一漏斗（``webm_clip._PopenCapture._wrapped``）
里，把刚创建的子进程句柄挂进模块级 Job Object（``KILL_ON_JOB_CLOSE``，实现见
``pet/win_job.py``）——父进程退出（正常或被强杀）时内核关闭它持有的 job 句柄，
最后一个句柄关闭即连带终止 job 内全部进程。

本文件用**真实进程边界**验证（不是 mock）：
- 修复组：子进程经产品漏斗拉起真实 ffmpeg → TerminateProcess 强杀子进程（父）
  → 断言 ffmpeg 退出；
- 对照组：子进程绕过漏斗（裸 ``subprocess.Popen``）拉起同款 ffmpeg → 同样强杀
  → 断言 ffmpeg **仍存活**（随后由测试自行清理）。对照组的存在证明第一条断言
  真能区分「有无 job」——否则它可能因为 ffmpeg 自己退出而永远是绿的。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != 'win32', reason='Job Object 是 Windows 内核机制',
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# 子进程脚本：经产品的 Popen 漏斗拉起真实 ffmpeg，上报 pid 后长睡。
# 命令行刻意**不往父进程管道写**（-f null + DEVNULL）：写管道的 ffmpeg 在父
# 进程被强杀、管道读端关闭时会自行以 EPIPE 退出——那样对照组和修复组都会
# 「自己死」，断言失去区分度。这里的 ffmpeg 只受内核 job 约束：`-re` 把无限
# lavfi 源限速到 1fps，保证它长期存活且零管道依赖。
_CHILD_TEMPLATE = r'''
import json, os, subprocess, sys, time
sys.path.insert(0, os.environ["DSH_PET_SRC_ROOT"])
from pet import webm_clip, win_job
import imageio_ffmpeg

webm_clip._PopenCapture._install()  # 与生产同源：读帧路径进入 capture 前必装
exe = imageio_ffmpeg.get_ffmpeg_exe()
argv = [exe, "-v", "error", "-re", "-f", "lavfi", "-i",
        "testsrc=size=64x64:rate=1", "-f", "null", "-"]
spawn = {spawn}
proc = spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.stdout.write(json.dumps({{"ffmpeg_pid": proc.pid,
                              "job_ready": win_job.is_available()}}) + "\n")
sys.stdout.flush()
time.sleep(300)
'''

_CHILD_VIA_HOOK = _CHILD_TEMPLATE.format(
    spawn='imageio_ffmpeg._io.subprocess.Popen')
# 对照组：裸 subprocess.Popen，绕过 job 挂载漏斗。
_CHILD_BYPASS = _CHILD_TEMPLATE.format(spawn='subprocess.Popen')


# ------------------------------------------------------------ Win32 小工具
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259


def _winapi():
    import ctypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return ctypes, kernel32


def _pid_alive(pid: int) -> bool:
    ctypes, kernel32 = _winapi()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_uint32(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _force_kill(pid: int) -> None:
    ctypes, kernel32 = _winapi()
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def _wait_pid_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _wait_pid_alive(pid: int, timeout: float) -> bool:
    """对照组用：给内核/调度一点时间确认它**没有**被连带杀掉。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return False
        time.sleep(0.1)
    return _pid_alive(pid)


def _spawn_child(src: str):
    env = os.environ.copy()
    env['DSH_PET_SRC_ROOT'] = str(REPO_ROOT)
    proc = subprocess.Popen(
        [sys.executable, '-c', src],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=env, cwd=str(REPO_ROOT),
    )
    lines: queue.Queue = queue.Queue()

    def _reader():
        try:
            lines.put(proc.stdout.readline())
        except Exception as exc:  # pragma: no cover - 读取失败只在病态下发生
            lines.put(f'__error__{exc}')

    threading.Thread(target=_reader, daemon=True).start()
    try:
        line = lines.get(timeout=90.0)
    except queue.Empty:  # pragma: no cover - CI 极慢时的兜底诊断
        proc.kill()
        raise AssertionError('子进程 90s 内未上报 ffmpeg pid')
    assert not line.startswith('__error__'), line
    assert line.strip(), _child_diagnostics(proc)
    return proc, json.loads(line)


def _child_diagnostics(proc) -> str:
    try:
        err = proc.stderr.read() if proc.stderr else ''
    except Exception:
        err = '<stderr 不可读>'
    return f'子进程无输出；stderr={err!r}'


# ------------------------------------------------------------ 修复组
def test_ffmpeg_spawned_through_hook_dies_with_killed_parent():
    """核心验收：经产品漏斗拉起的 ffmpeg 在父进程被强杀后退出。"""
    proc, payload = _spawn_child(_CHILD_VIA_HOOK)
    ffmpeg_pid = int(payload['ffmpeg_pid'])
    try:
        assert payload['job_ready'] is True, '子进程未能建立 Job Object'
        assert _pid_alive(ffmpeg_pid), 'ffmpeg 未成功拉起'
        # 强杀父进程（TerminateProcess，与 Stop-Process/任务管理器同路径）
        proc.kill()
        proc.wait(timeout=30)
        assert _wait_pid_gone(ffmpeg_pid, timeout=20.0), (
            f'父进程被强杀后 ffmpeg(pid={ffmpeg_pid}) 仍存活——'
            'Job Object 未生效（孤儿进程回归）'
        )
    finally:
        if proc.poll() is None:
            proc.kill()
        _force_kill(ffmpeg_pid)


# ------------------------------------------------------------ 对照组（证明断言可红）
def test_control_ffmpeg_bypassing_hook_survives_parent_kill():
    """对照：绕过漏斗（裸 Popen）的 ffmpeg 在父进程被强杀后**仍存活**。

    这条用例是第一条的「可红性证明」：同样的强杀手法、同样的素材与命令行，
    唯一的差异是有没有经 ``_PopenCapture._wrapped`` 挂进 job。若哪天第一条
    断言因为别的原因（ffmpeg 自己退出等）恒绿，这条会同时变红。
    """
    proc, payload = _spawn_child(_CHILD_BYPASS)
    ffmpeg_pid = int(payload['ffmpeg_pid'])
    try:
        assert _pid_alive(ffmpeg_pid), 'ffmpeg 未成功拉起'
        proc.kill()
        proc.wait(timeout=30)
        assert _wait_pid_alive(ffmpeg_pid, timeout=3.0), (
            f'对照组异常：未挂 job 的 ffmpeg(pid={ffmpeg_pid}) 竟随父进程退出——'
            '第一条用例的差异不再来自 Job Object，需重新审视验收方式'
        )
    finally:
        if proc.poll() is None:
            proc.kill()
        _force_kill(ffmpeg_pid)
        _wait_pid_gone(ffmpeg_pid, timeout=10.0)


# ------------------------------------------------------------ 单元面
def test_wrapped_popen_adopts_child_into_job(monkeypatch):
    """漏斗接线：``_PopenCapture._wrapped`` 必须把新建进程交给 win_job。"""
    from pet import webm_clip, win_job

    adopted = []
    monkeypatch.setattr(
        win_job, 'adopt', lambda proc: adopted.append(proc) or True,
    )
    proc = webm_clip._PopenCapture._wrapped(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
    )
    try:
        assert adopted == [proc], '漏斗未把子进程交给 win_job.adopt'
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_adopt_is_defensive_and_idempotent():
    """adopt：None/已退出进程/重复调用都不许抛异常，且可重复挂载同一 job。"""
    from pet import win_job

    assert win_job.adopt(None) is False
    dead = subprocess.Popen([sys.executable, '-c', 'pass'])
    dead.wait(timeout=30)
    assert win_job.adopt(dead) is False  # 已退出：只降级，不抛

    assert win_job.is_available() is True
    live = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        assert win_job.adopt(live) is True
        assert win_job.adopt(live) is True  # 幂等：重复挂载被内核吸收
        assert win_job._ensure_job() == win_job._ensure_job()  # 单例 job
    finally:
        live.kill()
        live.wait(timeout=10)
