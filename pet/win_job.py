# -*- coding: utf-8 -*-
"""Windows Job Object：ffmpeg 子进程随桌宠进程一起死（含被强杀）。

问题（2026-09-17 实机定案）：桌宠进程被 ``Stop-Process`` / 任务管理器结束
（= ``TerminateProcess``，父进程不执行任何清理代码）后，imageio-ffmpeg 拉起的
``ffmpeg-win-x86_64`` 子进程不会退出——实测残留一个 62MB 的常驻进程，每次强杀
/ 崩溃留一个（正常退出路径由 reader finally 的 terminate 收口，不受影响）。

为什么只能是 Job Object：``TerminateProcess`` 下父进程的 Python/C 代码一行都
不会执行——atexit / finally / 信号处理器 / 轮询线程全部不可能运行，唯一还在
工作的只有内核。Job Object 的 ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` 正是内核
语义：进程退出（含被杀）时内核关闭它持有的全部句柄，最后一个 job 句柄关闭
即杀掉 job 内所有成员进程。所以「父死子随」在被强杀场景下只有这一条路。

实现要点：
- **模块级单例 job，绝不 CloseHandle**：关掉自己的句柄就等于触发杀子；句柄
  由内核在进程退出时回收，那正是我们想要的触发点。
- **只挂 ffmpeg，不挂桌宠自身**：把当前进程挂进 job 会连带杀掉
  ``instance_launcher`` 明确要求「父桌宠退出后继续运行」的第二只桌宠，也无谓
  波及 node/pnpm 等由 agent_link 管理的会话进程。只对 imageio-ffmpeg 的 Popen
  唯一漏斗（``webm_clip._PopenCapture._wrapped``）拉起的子进程逐个 Assign。
- **挂早不挂晚**：Popen 返回后立即 Assign（进程刚创建，几乎不可能已退出）；
  进程已退出时 Assign 失败——静默忽略即可（进程已死，无需再杀）。
- **幂等**：重复调用只创建一次 job；重复 Assign 同一进程由内核吸收。
- **非 Windows 全无操作**：POSIX 上父进程死亡后子进程由 init 收养，本仓另有
  既有回收链，不在本轮范围。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == 'win32'

# JobObjectExtendedLimitInformation（winnt.h 的枚举序号；传给
# SetInformationJobObject 的 JobObjectInfoClass 参数）
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# «当最后一个 job 句柄关闭时杀掉 job 内所有进程» —— 本修复的全部机制
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# AssignProcessToJobObject 要求目标进程句柄带这两个权限
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_ulonglong),
        ('WriteOperationCount', ctypes.c_ulonglong),
        ('OtherOperationCount', ctypes.c_ulonglong),
        ('ReadTransferCount', ctypes.c_ulonglong),
        ('WriteTransferCount', ctypes.c_ulonglong),
        ('OtherTransferCount', ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', ctypes.c_uint32),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', ctypes.c_uint32),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', ctypes.c_uint32),
        ('SchedulingClass', ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION_STRUCT(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', _IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


_lock = threading.Lock()
_job_handle = None      # c_void_p 值（int）；永不 CloseHandle，见模块头
_configure_failed = False
_kernel32 = None


def _api():
    """惰性取 kernel32 并声明签名（不声明 argtypes 时 64 位句柄会被截断）。"""
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
    ]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32 = kernel32
    return kernel32


def _ensure_job():
    """创建并配置单例 job（幂等）；失败返回 None 并只告警一次。"""
    global _job_handle, _configure_failed
    if not _IS_WINDOWS or _configure_failed:
        return _job_handle
    with _lock:
        if _job_handle is not None or _configure_failed:
            return _job_handle
        try:
            kernel32 = _api()
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error(), 'CreateJobObjectW 失败')
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION_STRUCT()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                ctypes.c_void_p(handle),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise OSError(
                    ctypes.get_last_error(), 'SetInformationJobObject 失败',
                )
            _job_handle = handle
            logger.info(
                'ffmpeg 孤儿防护已就绪：Job Object（KILL_ON_JOB_CLOSE）——'
                '父进程被强杀时内核连带终止 ffmpeg 子进程',
            )
        except Exception as exc:
            _configure_failed = True
            logger.warning(
                'ffmpeg 孤儿防护不可用（Job Object 创建失败：%s）——'
                '强杀桌宠时残留的 ffmpeg 子进程不会被内核回收', exc,
            )
    return _job_handle


def is_available() -> bool:
    """Job Object 是否已成功建立（诊断/测试用）。"""
    return _ensure_job() is not None


def adopt(proc) -> bool:
    """把一个已创建的子进程挂进 kill-on-close job（幂等；非 Windows 无操作）。

    - 只挂进程，不做任何其它 Popen 操作（不 poll/wait/terminate/关管道）——
      与读者线程对 Popen 的独占所有权纪律不冲突（纯内核句柄操作）。
    - 进程已退出 / 句柄不可得：返回 False（进程已死，无需再杀），不抛异常。
    """
    if not _IS_WINDOWS or proc is None:
        return False
    handle = _ensure_job()
    if handle is None:
        return False
    try:
        pid = int(getattr(proc, 'pid', 0) or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    child = None
    owned = False
    try:
        raw = getattr(proc, '_handle', None)
        if raw:
            # Popen 自持的进程句柄（CreateProcess 返回，权限齐全）：借用，不关。
            child = ctypes.c_void_p(int(raw))
        else:
            child = ctypes.c_void_p(_api().OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid,
            ))
            owned = True
            if not child:
                return False
        if not _api().AssignProcessToJobObject(ctypes.c_void_p(handle), child):
            # 进程已退出（Assign 对已终止进程失败）是最常见的正常情形：忽略。
            logger.debug(
                'AssignProcessToJobObject 失败（pid=%s err=%s）：进程可能已退出',
                pid, ctypes.get_last_error(),
            )
            return False
        return True
    except Exception:
        return False
    finally:
        if owned and child:
            try:
                _api().CloseHandle(child)
            except Exception:
                pass


def _reset_for_tests() -> None:
    """仅测试用：允许重新建 job（生产代码绝不复位——句柄必须活到进程退出）。"""
    global _job_handle, _configure_failed
    with _lock:
        _job_handle = None
        _configure_failed = False
