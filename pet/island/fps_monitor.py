# -*- coding: utf-8 -*-
"""真实游戏帧率的采集生命周期（零 Qt；子进程、目标解析、时钟全部可注入）。

分工：**解析与统计在 ``pet/game_fps.py``**（纯函数），本模块只管"什么时候开一场
采集、跟谁、什么时候换、什么时候停、什么时候把读数发出去"。一次只有一个采集
会话在跑（串行换会话），订阅为零时**不建线程、不起进程、连目标都不枚举**。

数据源是官方 PresentMon 2.6.0 独立控制台程序（随 Windows 包分发，见
``game_fps.BACKEND_EXE_NAME`` / ``REQUIRED_SHA256``）：

- **按目标 PID 输出**（``--process_id``），一行一帧，读的是每一帧自带的
  ``MsBetweenPresents``（Present 间隔）/ ``MsBetweenDisplayChange``（上一帧的上屏
  时长）与 ``TimeInMs``（该帧在录制时间轴上的毫秒时刻）——**按事件时钟**算，
  不按 Python 收到多少行算（见 ``game_fps`` 的列契约说明）。
- **会话名每次唯一**（``dsh-pet-fps-<pid>-<seq>-<rand>``），停止只停自己那一场
  （``--terminate_existing_session``）。禁用 ``--stop_existing_session`` 与固定
  会话名：那是抢别人的采集会话。禁用 ``--restart_as_admin``：静默提权不在本功能
  的权限边界内（权限不足时如实报错，见下）。
- **权限边界**：Windows 上 ETW 会话需要管理员或 "Performance Log Users" 组。
  权限被拒时**不重试**（终态错误 + 官方原文），也不改用户组、不装服务、不提权。

线程纪律：

- 一条守护线程（``pet-fps-monitor``）负责"看目标 + 收行 + 发布快照"；每场采集
  会话另有**两条**读线程（stdout 行、stderr 尾巴），会话收口时一起结束。
- 停止默认**不 join**（``stop(wait=False)``）：收口要等下命令 + 有界等它退出，
  在 GUI 线程 join 会把"隐藏灵动岛"卡住。``close()`` 才做有界 join。
- **代次（generation）隔离**：``stop()`` 之后立刻 ``start()`` 时上一轮线程可能
  还在收尾——所以每轮线程自带 token，发布前核验"自己还是不是当前代"；每一场
  采集会话再带一个自增 ``generation`` 进快照，宿主据此丢弃迟到读数。
- **有界**：失败重试最多 :data:`MAX_CONSECUTIVE_FAILURES` 次且指数退避，权限类失败
  一次都不重试；行数、stderr 尾巴、原因串全有上限。**不做周期性会话回收**：
  到点换一场会话会在换的那一两秒里断数据（用户看到读数闪空），而它换来的只是
  "万一某场会话僵死"的兜底——真僵死的收口由停止路径负责（见 :meth:`FpsSession.stop`）。
- **读数不冒称实时**：帧自带事件时钟（``TimeInMs``，距今次录制的毫秒）时，用
  一次会话内的一次性校准把它换算到我们的挂钟上，任何"我们此刻读到的帧其实已经
  落后现实 N 秒"（管道积压 / 目标停出帧）都会被判成过期，而不是标上 ``sampled_at``
  当此刻的实时读数（见 :meth:`FpsMonitor._event_lag`）。

失败面必须分开呈现（``island_segments`` 的状态词表）：

===========================  ====================================================
状态                          含义
===========================  ====================================================
``unsupported``               非 Windows 或内置采集程序缺失（这台机器没有这个能力）
``sampling``                  会话在跑、窗口还没覆盖够（"采集中"）
``ok``                        有新鲜读数（``fps`` 不为 None）
``unknown``                   没有可采目标（没开游戏 / 桌宠在前台）、目标超过 N 秒
                              没有新帧（暂停、最小化、已退出）、或**数据已经落后
                              现实 N 秒**（管道积压）——**不伪报 0、也不冒称实时**
``error``                     后端失败（权限不足、异常退出、输出表头缺列）
===========================  ====================================================
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pet.island import game_fps
from pet import win_job

logger = logging.getLogger(__name__)

#: 前台目标必须**稳定**这么久才算数（Alt-Tab 掠过一堆窗口不该换会话）。
TARGET_STABLE_SECONDS = 0.75
#: 已经开着的会话至少活这么久才允许被换掉（第二道防抖：换会话要重开会话）。
MIN_SESSION_SECONDS = 1.0
#: 前台切到"不可采的东西"（桌宠自己 / 桌面外壳 / 没有前台窗口）后，**保持**当前
#: 目标多久。短暂切走（点一下桌宠）不该中断采集，超出就清值并停会话——
#: 但任何情况下都不改采别的进程（不许"顺手"测到别人）。
HOLD_TARGET_SECONDS = 5.0
#: 超过这么久没有新的 Present 帧 → 读数过期（暂停/最小化/目标已退出）。
#: 值会被清成 ``--``：把上一拍的 60fps 继续挂着，等于对用户说谎。
STALE_SECONDS = 3.0
#: 主循环节拍。10Hz 足够让目标切换与停止请求及时被看到，又不会白烧 CPU。
POLL_SECONDS = 0.1
#: 快照发布节拍（约 4Hz）：读数变化比这快没有意义，回调也不该按帧率刷 GUI。
PUBLISH_INTERVAL_SECONDS = 0.25
#: 连续失败几次后放弃自动重开（转为终态错误，等用户显式重试）。
MAX_CONSECUTIVE_FAILURES = 3
#: 失败重试的退避基数（秒，指数增长：2s / 4s / 8s）。
RETRY_BACKOFF_SECONDS = 2.0
#: PID → create_time 的复查间隔：同 pid 的新进程（PID 复用）必须被识别成新目标。
IDENTITY_RECHECK_SECONDS = 1.0
#: 按进程名找目标（``--process`` 模式）的重新扫描间隔。
RESCAN_SECONDS = 2.0
#: 单条官方终止命令的等待上限（秒）。
TERMINATE_TIMEOUT_SECONDS = 3.0
#: 一场会话收口的等待上限（秒）：等它自己退 → terminate → 必要时 kill。
STOP_TIMEOUT_SECONDS = 3.0
#: **退出收口**的外层 join 上限（秒）：必须盖住一场会话自己的最坏收口，否则"有界
#: 收口"会在 worker 还在硬杀子进程时就返回（子进程过几毫秒才死，解释器可能抢在
#: 前面退出 = 残留）。上限 = 等官方终止命令 + 等它自己退 + terminate 后再等一拍才 kill。
#: 正常情况 join 在 worker 一结束就返回（实机约 0.02–0.2s），这个上限只在
#: "PresentMon 既不理终止命令又不肯退"的病态路径上才用满。
EXIT_JOIN_TIMEOUT_SECONDS = TERMINATE_TIMEOUT_SECONDS + STOP_TIMEOUT_SECONDS + 1.0
#: 会话缓冲的行数与 stderr 尾巴上限（超出丢最老的：读数只关心最近几帧）。
MAX_BUFFERED_LINES = 4096
MAX_STDERR_LINES = 20
MAX_STDERR_CHARS = 400
#: 永不采的前台程序：桌面外壳（explorer.exe 在前台 = 用户在看桌面，不是游戏）。
DEFAULT_EXCLUDED_NAMES = frozenset({"explorer.exe"})
#: 主循环线程名（测试据此断言"没有线程泄漏"）。
THREAD_NAME = "pet-fps-monitor"


class FpsSessionError(RuntimeError):
    """一场采集会话起不来（后端缺失 / Popen 失败）。"""


@dataclass(frozen=True)
class FpsTarget:
    """采集目标：PID + 进程名 + 进程创建时刻（同 pid 的新进程靠它区分）。"""

    pid: int
    name: str
    created_at: float | None = None

    def same(self, other: "FpsTarget | None") -> bool:
        if other is None:
            return False
        if self.pid != other.pid:
            return False
        if self.created_at is not None and other.created_at is not None:
            return self.created_at == other.created_at
        return True


@dataclass(frozen=True)
class FpsSample:
    """一个发布周期的读数快照（GUI 线程只读它，不做任何 syscall）。

    ``fps`` 是**目标应用的 Present 帧率**（不是上屏帧率、不是 GPU 占用）：
    无读数时恒为 ``None``（呈现层画 ``--``），**绝不伪报 0**。
    ``displayed_fps`` 只在有上屏时长样本时给（详情用），与 ``fps`` 不混算。
    ``detail`` 是卡片 B 区的行素材（口径、来源、目标、链数），键与 ``gpu_monitor``
    同一套（``unit_full`` / ``source`` / ``detail`` / ``error_reason``…）。
    """

    fps: float | None
    state: str
    sampled_at: float
    target_pid: int | None = None
    target_name: str = ""
    error_reason: str = ""
    displayed_fps: float | None = None
    generation: int = 0
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------- 会话（子进程）
def _creation_flags() -> int:
    """Windows：隐藏控制台窗口。缺了它每次开会话都闪一个终端（默认终端承载）。"""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


class FpsSession:
    """一场 PresentMon 采集会话：一个子进程 + 两条读线程 + 有界收口。

    公开契约（替身测试按它打桩，见 ``tests/test_fps_monitor.py``）::

        start()            起采集进程（后端缺失抛 FpsSessionError）
        drain_lines()      取走自上次调用以来收到的行（有界缓冲）
        stderr_tail()      最近若干行 stderr（失败归类的原料）
        poll()             进程退出码；还在跑返回 None
        stop(timeout=...)  官方终止命令 → 有界等退 → terminate → kill
        close()            收口线程与管道（幂等）
    """

    def __init__(
        self,
        *,
        pid: int,
        name: str,
        backend: Any,
        session_name: str,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
        terminate_timeout: float = TERMINATE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.pid = int(pid)
        self.target_name = str(name)
        self.backend = Path(backend)
        self.session_name = str(session_name)
        self._stop_timeout = float(stop_timeout)
        self._terminate_timeout = float(terminate_timeout)
        self._clock = clock
        self._sleep = sleep
        self._proc = None
        self._lines: deque[str] = deque(maxlen=MAX_BUFFERED_LINES)
        self._stderr: deque[str] = deque(maxlen=MAX_STDERR_LINES)
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._closing = threading.Event()

    # ------------------------------------------------------------ 状态
    @property
    def argv(self) -> list[str]:
        """本场会话的采集命令行（证据与测试用；每次按当前参数现算）。"""
        return game_fps.build_capture_args(session_name=self.session_name, pid=self.pid)

    def start(self) -> None:
        """起采集进程并挂上读线程。后端不可用/进程起不来都抛 FpsSessionError。"""
        problem = game_fps.backend_problem(self.backend)
        if problem:
            raise FpsSessionError(problem)
        argv = [str(self.backend)] + self.argv
        try:
            proc = subprocess.Popen(  # noqa: S603 - 参数逐个拼装，绝不过 shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise FpsSessionError(f"采集进程启动失败：{exc}") from exc
        self._proc = proc
        # 父进程被强杀（TerminateProcess）时 Python 一行清理代码都不会跑，
        # 只有内核还认这个 job —— 采集进程必须挂进去，否则杀桌宠会留下孤儿采集。
        win_job.adopt(proc)
        self._spawn_reader(proc)

    def _spawn_reader(self, proc) -> None:
        """挂上 stdout 行读取与 stderr 尾巴两条线程（会话收口时结束）。"""
        self._closing.clear()
        self._threads = [
            self._new_thread(f"{THREAD_NAME}-stdout", self._read_stdout, proc.stdout),
            self._new_thread(f"{THREAD_NAME}-stderr", self._read_stderr, proc.stderr),
        ]
        for thread in self._threads:
            thread.start()

    def _new_thread(self, name, target, stream):
        return threading.Thread(
            target=target, args=(stream,), daemon=True, name=name,
        )

    def _read_stdout(self, stream) -> None:
        """读 stdout 的 CSV 行：**超长行整行丢弃**，绝不截断后当数据解析。

        ``readline(MAX_LINE_CHARS)`` 保证单次读取有上界（否则一行损坏的数据就能
        把内存吃光）；返回的长度已经封顶又没见到换行，就只能是一个超长行——
        丢掉它直到下一个换行为止（截断后的半个字段解析出来是错的列值）。
        """
        carry = ""
        dropping = False
        try:
            while not self._closing.is_set():
                chunk = stream.readline(game_fps.MAX_LINE_CHARS)
                if not chunk:
                    break
                if dropping:
                    if "\n" in chunk:
                        dropping = False
                    continue
                line = carry + chunk
                carry = ""
                if line.endswith("\n") or line.endswith("\r"):
                    self._append_line(line)
                elif len(chunk) >= game_fps.MAX_LINE_CHARS:
                    dropping = True
                else:
                    carry = line
            if carry:
                self._append_line(carry)
        except (ValueError, OSError):
            # 管道被关闭（进程退出 / 我们主动收口）：正常路径，不是错误。
            pass
        except Exception:  # pragma: no cover - 读线程异常不该外溢
            logger.debug("帧率采集：stdout 读取异常", exc_info=True)

    def _read_stderr(self, stream) -> None:
        try:
            while not self._closing.is_set():
                line = stream.readline(MAX_STDERR_CHARS)
                if not line:
                    break
                text = line.rstrip("\r\n").strip()
                if text:
                    with self._lock:
                        self._stderr.append(text[:MAX_STDERR_CHARS])
        except (ValueError, OSError):
            pass
        except Exception:  # pragma: no cover
            logger.debug("帧率采集：stderr 读取异常", exc_info=True)

    def _append_line(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    # ------------------------------------------------------------ 读取
    def drain_lines(self) -> list[str]:
        """取走缓冲里的行（有界缓冲：拿不到的只有更老的那些帧，读数不受影响）。"""
        with self._lock:
            lines = list(self._lines)
            self._lines.clear()
        return lines

    def stderr_tail(self) -> str:
        with self._lock:
            return "\n".join(self._stderr)

    def poll(self) -> int | None:
        proc = self._proc
        return None if proc is None else proc.poll()

    # ------------------------------------------------------------ 收口
    def stop(self, timeout: float | None = None, *, terminate_session: bool = True) -> None:
        """停止本场采集：官方终止命令 → 有界等它自己退 → terminate → kill。

        **不用 CTRL_BREAK**：采集进程以 ``CREATE_NO_WINDOW`` 拉起（没有可见控制台），
        信号能不能送达取决于进程组与控制台的绑定，赌不起；``terminate()`` 是硬杀，
        会把 ETW 会话留在系统里。官方的 ``--terminate_existing_session`` 指名停掉
        我们这一场会话，进程随后自行退出——这是唯一既干净又不越界的方式。

        ``terminate_session=False``（会话结束/关机路径用）：**跳过那条终止命令**。
        它是一次 ``Popen``——而关机窗口正是最不该再派生进程的时刻（issue #111 的
        0xc0000142 就是会话拆除阶段 CreateProcess 出来的进程在 DLL 初始化时失败），
        所以那条路径只做有界硬停（terminate → kill）。**并且不等那个预算**：没有
        "官方终止命令"这条自退路径时，等待阶段没有任何东西可等，白等 `stop_timeout`
        秒等于把关机拖住。随登录会话销毁的 ETW 会话由内核回收，**本机无采集权限、
        未实机验证**（见 ``docs/PR-REPORT-GAME-FPS-2026-10-11.md``）。
        """
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        budget = self._stop_timeout if timeout is None else float(timeout)
        if terminate_session:
            self._run_terminate_command()
            deadline = self._clock() + max(0.0, budget)
            while self._clock() < deadline and proc.poll() is None:
                self._sleep(0.02)
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:  # pragma: no cover - 进程恰好已退出
                pass
            kill_deadline = self._clock() + min(1.0, max(0.2, budget))
            while self._clock() < kill_deadline and proc.poll() is None:
                self._sleep(0.02)
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover
                pass

    def _run_terminate_command(self) -> None:
        """跑官方终止命令（有界，超时升 terminate/kill），失败只记日志。"""
        argv = [str(self.backend)] + game_fps.build_terminate_args(self.session_name)
        try:
            proc = subprocess.Popen(  # noqa: S603 - 同上，绝不过 shell
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except OSError:
            logger.debug("帧率采集：终止命令启动失败（继续按有界等待收口）", exc_info=True)
            return
        win_job.adopt(proc)
        try:
            proc.communicate(timeout=self._terminate_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=1.0)
            except Exception:  # pragma: no cover
                pass
        except Exception:  # pragma: no cover
            logger.debug("帧率采集：终止命令等待异常", exc_info=True)

    def close(self) -> None:
        """收口读线程与进程（幂等；有界 join，卡死也不吊住调用方）。"""
        self._closing.set()
        proc = self._proc
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        for thread in self._threads:
            thread.join(timeout=0.5)
        self._threads = []


# ---------------------------------------------------------------- 目标解析（系统面）
_foreground_api = None

#: 残留清理的"一个进程只扫一次"闸门（``logman query -ets`` 实测约 970ms）。
_swept_stale_sessions = False

#: 本进程创建过的全部采集会话名：清理时**一律不碰**（见 ``sweep_stale_sessions``）。
_own_session_names: set[str] = set()


def sweep_stale_sessions(session_name: str, *, once: bool = True) -> list[str]:
    """开会话前清掉**自己前缀**的历史残留 ETW 会话，返回停掉的名字。

    残留来自异常退出（进程被强杀/崩溃时走不到 ``--terminate_existing_session``）。
    Windows 的 trace 会话有上限，攒到十几个以后新会话直接报 ``error 1450``
    （系统资源不足）——实机现场：整段 FPS 不出数，日志只有一行 1450，而用户
    以前单屏玩游戏能出数（权限没变，是我们自己的会话攒满了）。

    只停 ``dsh-pet-fps-`` 前缀、且**不是本进程建过的任何一场会话**的名字：绝不用
    通配去抢别人的采集会话（同 ``new_session_name`` 的纪律）。只排除"本次要开的
    那个名字"是不够的——``stop(wait=False)`` 之后立刻 ``start()`` 时，旧 worker 可能
    还卡在这次查询里，新的采集会话已经起来了，只认本次名字就会把它当残留停掉。
    清理是尽力而为，任何失败都只放弃清理，绝不影响随后真正要开的采集会话。

    ``once=True``（产品路径）：**一个进程只成功扫一次**。残留只可能来自上一个
    被强杀的进程，而 ``logman query -ets`` 实测 970ms 中位、``logman stop`` 56ms
    ——每换一次游戏目标重付一次查询没有收益。查询失败时闸门不关，下一次还会再试。
    ``once=False`` 供测试与显式排查复扫。
    """
    global _swept_stale_sessions
    _own_session_names.add(str(session_name))
    if once and _swept_stale_sessions:
        return []
    try:
        listed = subprocess.run(  # noqa: S603 - 固定 argv，绝不过 shell
            ["logman", "query", "-ets"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
            creationflags=_creation_flags())
    except (OSError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        # 读不到会话表 = 这次没扫成，**闸门不关**，下一次开会话再试（与发布稿
        # 「查询失败不关闸」一致；只有真正拿到表才算扫过）。
        logging.warning("帧率采集：列出残留 ETW 会话失败（logman rc=%s），本次不清理",
                        listed.returncode)
        return []
    _swept_stale_sessions = True
    names = [name for name in
             game_fps.stale_session_names(listed.stdout or "", keep=session_name)
             if name not in _own_session_names]
    stopped: list[str] = []
    for name in names:
        try:
            done = subprocess.run(  # noqa: S603 - 固定 argv
                game_fps.stop_session_args(name),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
                creationflags=_creation_flags())
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            stopped.append(name)
    if stopped:
        logging.warning("帧率采集：清理 %d 个残留 ETW 会话（异常退出留下的）：%s",
                        len(stopped), ", ".join(stopped))
    return stopped


def default_session(pid: int, name: str, session_name: str, backend: Any = None) -> FpsSession:
    """普通权限会话的构造（:class:`FpsMonitor` 与岛侧的替换点共用）。

    采集只有这一条路：没有第二个后端可选，所以内置后端的解析只在这里实现一份，
    测试替身与产品走的是同一个入口。开新会话前先清自己前缀的残留会话——这一步
    放在工厂而不是 ``FpsSession.start()``：清残留是"准备新采集"的前置动作，
    不是某个会话的生命周期，且它要再起两个 ``logman`` 子进程，不该被会话自身
    的进程替身记账。
    """
    if backend is None:
        backend = game_fps.bundled_backend_path()
    sweep_stale_sessions(session_name)
    return FpsSession(pid=pid, name=name, backend=backend, session_name=session_name)


def _foreground_api_instance():
    """``pet/memory_trim.py`` 的 Win32 薄封装（惰性、进程内单例）。

    复用它而不是照抄一份 ctypes：那个类就是 kernel32/psapi/user32 的封装，公开、
    有测试覆盖，import 实测 66ms 且不带 psutil。**不用 vision 模块**：它为截图/OCR
    拖进 base64/json/urllib（import 实测 158ms），跟随前台每次切窗口都要问一次，
    不值得为省十几行 ctypes 付这个成本。
    """
    global _foreground_api
    if _foreground_api is None:
        from pet.memory_trim import Win32ProcessApi
        _foreground_api = Win32ProcessApi()
    return _foreground_api


def default_foreground_reader() -> tuple[int, str] | None:
    """前台窗口的 ``(pid, 进程名)``；取不到返回 ``None``（不抛）。"""
    if not game_fps.platform_support():
        return None
    try:
        api = _foreground_api_instance()
        pid = api.foreground_pid()
        if not pid:
            return None
        handle = api.open_process(pid)
        if not handle:
            return (int(pid), "")
        try:
            name = api.image_name(handle)
        finally:
            api.close_handle(handle)
        return (int(pid), str(name or ""))
    except Exception:
        logger.debug("帧率采集：前台窗口查询失败", exc_info=True)
        return None


def default_process_identity(pid: int) -> float | None:
    """进程创建时刻（PID 复用隔离）；psutil 缺失或查不到返回 ``None``。

    ``None`` 表示"身份未知"，此时**不**据此换目标（宁可多采一小会儿同一个 pid，
    也不因为查不到就把采集目标丢掉）。
    """
    try:
        import psutil
    except Exception:  # pragma: no cover - 环境缺 psutil
        return None
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def default_process_lookup(name: str) -> tuple[int, float] | None:
    """按 exe 名找目标进程 → ``(pid, create_time)``；找不到返回 ``None``。

    同一 exe 有多个实例时取**最近创建**的那个（用户刚启动的最可能是想测的那个），
    并记一条 info 日志说明选了谁——绝不按"谁帧率高"猜目标。
    """
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    try:
        import psutil
    except Exception:  # pragma: no cover
        return None
    best: tuple[int, float] | None = None
    try:
        for proc in psutil.process_iter(["pid", "name", "create_time"]):
            info = getattr(proc, "info", None) or {}
            if str(info.get("name") or "").lower() != wanted:
                continue
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            created = float(info.get("create_time") or 0.0)
            if best is None or created > best[1]:
                best = (pid, created)
    except Exception:
        logger.debug("帧率采集：按进程名定位失败", exc_info=True)
        return None
    if best is not None and best[0] != os.getpid():
        logger.info("帧率采集：进程名 %s 匹配到 pid=%s（多实例取最近创建）", name, best[0])
    return best


def _own_excluded_names() -> frozenset[str]:
    """自己的可执行文件名（前台是桌宠自己时不采；pid 判定是第一道，这是第二道）。"""
    names = set(DEFAULT_EXCLUDED_NAMES)
    for candidate in (getattr(sys, "executable", ""),):
        base = os.path.basename(str(candidate or "")).lower()
        if base:
            names.add(base)
    return frozenset(names)


# ---------------------------------------------------------------- 运行时（每轮线程私有）
@dataclass
class _Runtime:
    """一轮采集线程的私有状态（跨拍保留；停止/换代时随线程一起丢弃）。"""

    window: Any
    epoch: int = 0
    session: Any = None
    columns: dict | None = None
    target: FpsTarget | None = None
    session_started_at: float = 0.0
    #: 本场会话用的事件时钟列名（``""`` = 没有事件时钟，退回到达时钟）。
    clock_column: str = ""
    #: 事件时钟 → 我们挂钟的一次性校准：``offset = 收到它的时刻 - 事件秒``。
    #: ``None`` = 还没校到（第一帧到来时建立）。
    clock_offset: float | None = None
    candidate: FpsTarget | None = None
    candidate_since: float = 0.0
    hold_since: float | None = None
    no_target_reason: str = ""
    problem: str = ""
    #: 后端 stderr 原文（有界）：卡片上只放一句可行动的话，证据放这里。
    raw_error: str = ""
    last_publish_at: float = 0.0
    last_state: str = ""
    failures: int = 0
    next_attempt_at: float = 0.0
    identity_checked_at: float = 0.0
    rescan_at: float = 0.0


# ---------------------------------------------------------------- 监控器
class FpsMonitor:
    """游戏帧率采集的对外入口（宿主只碰它，不碰子进程）。

    生命周期由调用方按"开关开着且岛可见"驱动：``start()`` / ``stop()``；
    段自己按显示与否 ``subscribe(key)`` / ``unsubscribe(key)``。**订阅数为 0 时
    不建线程、不起进程、不枚举目标**——关掉这段就该是真的零开销。

    ``configure(mode, process)`` 由设置页写入：``mode="foreground"`` 自动跟随前台，
    ``mode="process"`` 只跟指定 exe 名。改动即时生效（正在采集则换目标）。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        foreground_reader: Callable[[], tuple[int, str] | None] | None = None,
        process_identity: Callable[[int], float | None] | None = None,
        process_lookup: Callable[[str], tuple[int, float] | None] | None = None,
        session_factory: Callable[[int, str, str], Any] | None = None,
        backend_path: Any = None,
        platform_supported: Callable[[], bool] | None = None,
        on_sample: Callable[[FpsSample], None] | None = None,
        target_stable_seconds: float = TARGET_STABLE_SECONDS,
        min_session_seconds: float = MIN_SESSION_SECONDS,
        hold_seconds: float = HOLD_TARGET_SECONDS,
        stale_seconds: float = STALE_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        publish_interval: float = PUBLISH_INTERVAL_SECONDS,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
        retry_backoff_seconds: float = RETRY_BACKOFF_SECONDS,
        identity_recheck_seconds: float = IDENTITY_RECHECK_SECONDS,
        rescan_seconds: float = RESCAN_SECONDS,
        excluded_names: frozenset[str] | None = None,
        self_pid: int | None = None,
    ):
        self._clock = clock
        self._foreground_reader = foreground_reader or default_foreground_reader
        self._process_identity = process_identity or default_process_identity
        self._process_lookup = process_lookup or default_process_lookup
        self._session_factory = session_factory or self._default_session_factory
        self._backend_path = backend_path
        self._platform_supported = platform_supported or game_fps.platform_support
        self._on_sample = on_sample
        self._target_stable_seconds = float(target_stable_seconds)
        self._min_session_seconds = float(min_session_seconds)
        self._hold_seconds = float(hold_seconds)
        self._stale_seconds = float(stale_seconds)
        self._poll_seconds = max(0.001, float(poll_seconds))
        self._publish_interval = max(0.0, float(publish_interval))
        self._max_failures = max(1, int(max_failures))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._identity_recheck_seconds = max(0.0, float(identity_recheck_seconds))
        self._rescan_seconds = max(0.0, float(rescan_seconds))
        self._excluded_names = (excluded_names if excluded_names is not None
                                else _own_excluded_names())
        self._self_pid = int(os.getpid() if self_pid is None else self_pid)

        self._lock = threading.Lock()
        self._subscribers: set[str] = set()
        self._enabled = False
        self._mode = "foreground"
        self._process = ""
        self._epoch = 0
        self._generation = 0
        self._snapshot: FpsSample | None = None
        self._fatal_reason = ""
        self._token: object | None = None
        self._thread: threading.Thread | None = None
        #: 上一轮被停掉的线程（换代清空 ``_thread`` 后，只有这里还能 join 到它）。
        self._last_stopped_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        #: 收口时要不要发官方终止命令（见 ``stop``）：默认要；会话结束路径置假。
        self._terminate_session = True
        self._fallback = self._build_fallback()

    # ------------------------------------------------------------ 构造现场
    def _default_session_factory(self, pid: int, name: str, session_name: str) -> FpsSession:
        return default_session(pid, name, session_name, self._backend_path)

    # ------------------------------------------------------------ 状态
    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def process(self) -> str:
        return self._process

    @property
    def subscribers(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._subscribers)

    def snapshot(self) -> FpsSample:
        """最近一次发布的读数（还没跑完第一轮时按能力/开关给出降级样本）。

        线程安全；**每次调用只读一份对象**，不做任何 syscall（宿主按岛刷新率调它）。
        """
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot
            return self._fallback

    def _blocked(self) -> tuple[str, str]:
        """能力层面的阻塞原因 ``(状态, 原因)``；可用返回 ``("", "")``。

        平台判据**只问注入的那一次**（:attr:`_platform_supported`）：``backend_state``
        会再查一次真实 ``sys.platform``，把"注入的能力可用"在非 Windows 宿主上静默压掉。
        后端只查文件（``backend_problem`` 不碰平台），缺程序照样 ``unsupported`` 带路径。
        """
        if not self._platform_supported():
            return game_fps.STATE_UNSUPPORTED, self._platform_blocked_reason()
        problem = game_fps.backend_problem(self._backend_path)
        if problem:
            return game_fps.STATE_UNSUPPORTED, problem
        return "", ""

    def _platform_blocked_reason(self) -> str:
        """平台/架构闸门的人话原因（非 Windows / ARM64 未验证 / 架构不匹配）。

        ``platform_supported`` 可注入（替身测试替换它），而真实的架构判定替换不了，
        所以注入版本说"没有能力"、真实原因却取不到时，回落到非 Windows 那一句——
        替身用例的行为与本批之前逐字一致，不因新增架构判据而变。
        """
        return game_fps.platform_problem() or game_fps.PLATFORM_UNSUPPORTED_REASON

    def _build_fallback(self) -> FpsSample:
        """没有快照时的呈现：能力不足 > 未开启 > 没有订阅者。"""
        state, reason = self._blocked()
        if not state:
            with self._lock:
                enabled, subscribed = self._enabled, bool(self._subscribers)
            if not enabled:
                state, reason = game_fps.STATE_UNKNOWN, "尚未开始采集"
            elif not subscribed:
                state, reason = game_fps.STATE_UNKNOWN, "没有在显示的帧率段"
        return FpsSample(
            fps=None, state=state, sampled_at=self._clock(), error_reason=reason,
            detail=self._detail(state=state, reason=reason),
        )

    def _refresh_fallback(self) -> None:
        fallback = self._build_fallback()
        with self._lock:
            self._fallback = fallback

    # ------------------------------------------------------------ 订阅与开关
    def subscribe(self, key: str) -> bool:
        """登记一个订阅者（段 id / 宿主名）。返回是否真的会去采集。"""
        with self._lock:
            self._subscribers.add(str(key))
        self._refresh_fallback()
        blocked = self._blocked()
        if blocked[0]:
            return False
        return self._maybe_start_worker()

    def unsubscribe(self, key: str) -> None:
        """注销订阅者；**最后一个订阅者消失即停采**（线程与子进程一起收）。"""
        with self._lock:
            self._subscribers.discard(str(key))
            empty = not self._subscribers
        if empty:
            self._stop_worker(wait=False)
        self._refresh_fallback()

    def configure(self, mode: str = "foreground", process: str = "") -> None:
        """设置跟随方式（前台自动 / 指定 exe 名）。运行中即时换代重新解析目标。"""
        normalized = "process" if str(mode or "").strip().lower() == "process" else "foreground"
        wanted = str(process or "").strip() if normalized == "process" else ""
        with self._lock:
            changed = (self._mode, self._process) != (normalized, wanted)
            self._mode, self._process = normalized, wanted
            if changed:
                self._epoch += 1
                # 用户显式改了配置 = 一次显式重试：终态错误在这里解除。
                self._fatal_reason = ""
        self._refresh_fallback()
        if changed:
            self._maybe_start_worker()

    def start(self) -> bool:
        """开始（岛可见 / 开关打开）。**恢复时先清旧值**：上一版读数不是此刻的读数。"""
        with self._lock:
            self._enabled = True
            self._fatal_reason = ""
            self._snapshot = None
        self._refresh_fallback()
        return self._maybe_start_worker()

    def stop(self, wait: bool = False, timeout: float = 2.0, *,
             terminate_session: bool = True) -> None:
        """停止采集（隐藏岛 / 关开关 / 退出）。

        默认**不 join**：收口要发终止命令 + 有界等它退出，在 GUI 线程 join 会把
        "隐藏灵动岛"卡住；换代的 token 保证在途结果不再被发布。``wait=True`` 仅
        测试与退出路径用（至多阻塞 ``timeout`` 秒）。

        ``terminate_session=False``（会话结束/关机路径）：收口**不再派生**那条官方
        终止命令（理由见 ``FpsSession.stop``）。
        """
        with self._lock:
            self._enabled = False
            self._snapshot = None
            self._terminate_session = bool(terminate_session)
        self._stop_worker(wait=wait, timeout=timeout)
        self._refresh_fallback()

    def close(self, *, terminate_session: bool = True) -> None:
        """最终收口（退出路径）：停线程并**有界** join，幂等。

        join 上限用 :data:`EXIT_JOIN_TIMEOUT_SECONDS`（盖住会话自己的最坏收口）——
        比 ``STOP_TIMEOUT_SECONDS`` 大，正是为了让"返回即已收完"这句话成立。
        """
        self.stop(wait=True, timeout=EXIT_JOIN_TIMEOUT_SECONDS,
                  terminate_session=terminate_session)

    # ------------------------------------------------------------ 线程管理
    def _maybe_start_worker(self) -> bool:
        # 能力不足（非 Windows / 缺内置采集程序）时不建线程：没有可做的事，
        # 建一条空转线程只会把"这台机器没有这个能力"变成"看起来在采集中"。
        if self._blocked()[0]:
            return False
        with self._lock:
            if not self._enabled or not self._subscribers:
                return False
            if self._thread is not None and self._thread.is_alive():
                return True
            event = threading.Event()
            token = object()
            self._stop_event = event
            self._token = token
            thread = threading.Thread(
                target=self._run, args=(event, token), daemon=True, name=THREAD_NAME,
            )
            self._thread = thread
        thread.start()
        return True

    def _stop_worker(self, *, wait: bool, timeout: float = 2.0) -> None:
        event, self._stop_event = self._stop_event, None
        if event is not None:
            event.set()
        thread, self._thread = self._thread, None
        with self._lock:
            # 换代：在途（已退订/已停止）的那一轮不许再发布读数。
            self._token = None
        # **留着上一轮的线程引用**：换代纪律把 ``self._thread`` 清空（本方法 + ``_run``
        # 的 finally 都清），所以非阻塞 ``stop()`` 之后再也没有人能 join 到它——
        # 于是"退出收口"看起来做了、其实把会话收口丢给守护线程就返回了，解释器随后
        # 退出就留下采集进程与它那场独占 ETW 会话。有界收口（``close``）必须能等到
        # **上一轮**真的跑完（worker 的 finally 里就是 ``session.stop/close``）。
        if thread is not None:
            self._last_stopped_thread = thread
        target = thread if thread is not None else self._last_stopped_thread
        if not wait or target is None or target is threading.current_thread():
            return
        target.join(timeout)
        if not target.is_alive():
            self._last_stopped_thread = None

    # ------------------------------------------------------------ 主循环
    def _run(self, stop: threading.Event, token: object) -> None:
        runtime = _Runtime(window=game_fps.FrameWindows())
        try:
            while not stop.is_set():
                now = self._clock()
                with self._lock:
                    active = bool(self._subscribers) and self._enabled
                    epoch = self._epoch
                if not active:
                    break
                if epoch != runtime.epoch:
                    runtime.epoch = epoch
                    self._close_session(runtime, drop_target=True)
                self._supervise(runtime, now)
                self._pump(runtime)
                self._check_backend(runtime, now)
                self._publish(runtime, now, token)
                if stop.wait(self._poll_seconds):
                    break
        except Exception:  # pragma: no cover - 守护线程里不许把异常抛给解释器
            logger.exception("帧率采集：主循环异常退出")
        finally:
            self._close_session(runtime, drop_target=True)
            with self._lock:
                if self._token is token:
                    self._token = None
                    self._thread = None

    # ------------------------------------------------------------ 目标监督
    def _supervise(self, runtime: _Runtime, now: float) -> None:
        """决定这一拍该采谁：解析目标 → 去抖 → 换会话；没有目标时按保持窗口处理。"""
        if self._mode == "process":
            candidate, reason = self._process_target(runtime, now)
        else:
            candidate, reason = self._foreground_target(runtime, now)

        if candidate is None:
            runtime.no_target_reason = reason
            runtime.candidate = None
            if runtime.target is not None:
                runtime.hold_since = now if runtime.hold_since is None else runtime.hold_since
                if now - runtime.hold_since > self._hold_seconds:
                    self._close_session(runtime, drop_target=True)
            return

        runtime.hold_since = None
        runtime.no_target_reason = ""
        if runtime.target is not None and candidate.same(runtime.target):
            if runtime.session is not None:
                return
            if self._session_blocked(runtime, now):
                return
            self._open_session(runtime, candidate, now)
            return

        if runtime.candidate is None or not candidate.same(runtime.candidate):
            runtime.candidate = candidate
            runtime.candidate_since = now
            return
        if now - runtime.candidate_since < self._target_stable_seconds:
            return
        if (runtime.session is not None
                and now - runtime.session_started_at < self._min_session_seconds):
            return
        if self._session_blocked(runtime, now):
            return
        self._open_session(runtime, candidate, now)

    def _session_blocked(self, runtime: _Runtime, now: float) -> bool:
        """现在能不能开会话：终态错误未解除，或还在失败退避窗口里 → 不能。"""
        if self._fatal_reason:
            return True
        return now < runtime.next_attempt_at

    def _foreground_target(self, runtime: _Runtime, now: float) -> tuple[FpsTarget | None, str]:
        """前台目标 + 不可采时的人话原因。"""
        raw = self._foreground_reader()
        if not raw:
            return None, "没有前台窗口"
        try:
            pid = int(raw[0])
        except (TypeError, ValueError, IndexError):
            return None, "没有前台窗口"
        name = str(raw[1] or "") if len(raw) > 1 else ""
        if pid <= 0:
            return None, "没有前台窗口"
        if pid == self._self_pid:
            return None, "前台是桌宠自己（不测自己）"
        if name and name.lower() in self._excluded_names:
            return None, f"前台是系统外壳程序 {name}（不是游戏）"
        return self._with_identity(runtime, pid, name, now), ""

    def _with_identity(self, runtime: _Runtime, pid: int, name: str,
                       now: float) -> FpsTarget:
        """补上进程创建时刻（PID 复用隔离）。

        只在 pid 变化或复查窗口到点时查一次：create_time 是"这个 pid 还是不是
        那个进程"的唯一凭证，同 pid 的新进程必须被当成**新目标**（否则旧窗口的
        读数会被错记到新进程头上）。
        """
        current = runtime.target
        if (current is not None and current.pid == pid
                and now - runtime.identity_checked_at <= self._identity_recheck_seconds):
            return current
        created = self._process_identity(pid)
        runtime.identity_checked_at = now
        if created is None and current is not None and current.pid == pid:
            return current
        return FpsTarget(pid=pid, name=name, created_at=created)

    def _process_target(self, runtime: _Runtime, now: float) -> tuple[FpsTarget | None, str]:
        """按 exe 名定位目标（``--process`` 模式）；扫描按 ``rescan_seconds`` 限频。"""
        name = self._process
        if not name:
            return None, "没有指定要跟随的程序名"
        current = runtime.target
        if current is not None and current.name.lower() == name.lower():
            return current, ""
        # 去抖期间的重扫节流：已经找到、正在等它稳定时直接把那个候选还回去，
        # 否则每拍都会因为"这一拍没扫"而把候选清成 None，去抖永远走不到头。
        pending = runtime.candidate
        if pending is not None and pending.name.lower() == name.lower():
            return pending, ""
        if now < runtime.rescan_at:
            return None, f"没有找到正在运行的进程 {name}"
        runtime.rescan_at = now + self._rescan_seconds
        found = self._process_lookup(name)
        if not found:
            return None, f"没有找到正在运行的进程 {name}"
        try:
            pid = int(found[0])
            created = float(found[1])
        except (TypeError, ValueError, IndexError):
            return None, f"进程 {name} 的定位结果不可用"
        if pid <= 0 or pid == self._self_pid:
            return None, f"进程 {name} 的定位结果不可用"
        return FpsTarget(pid=pid, name=name, created_at=created), ""

    # ------------------------------------------------------------ 会话开合
    def _open_session(self, runtime: _Runtime, target: FpsTarget, now: float) -> None:
        """换到新目标：**先停旧的再起新的**（一次只有一个采集会话）。"""
        self._close_session(runtime, drop_target=True)
        # 换代即清窗：上一段的帧区间绝不许跨目标残留（那是"把别的东西的帧
        # 算到新目标头上"的另一条路径）。
        runtime.window.reset()
        session_name = game_fps.new_session_name()
        try:
            session = self._session_factory(target.pid, target.name, session_name)
            session.start()
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            self._note_failure(runtime, 1, reason)
            logger.info("帧率采集：会话启动失败：%s", reason)
            return
        runtime.session = session
        runtime.columns = None
        runtime.clock_column = ""
        runtime.clock_offset = None
        runtime.problem = ""
        runtime.raw_error = ""
        runtime.target = target
        runtime.session_started_at = now
        with self._lock:
            self._generation += 1
        logger.info(
            "帧率采集：开始会话（pid=%s name=%s session=%s，第 %s 场）",
            target.pid, target.name or "?", session_name, self._generation,
        )

    def _close_session(self, runtime: _Runtime, *, drop_target: bool) -> None:
        session, runtime.session = runtime.session, None
        runtime.columns = None
        if drop_target:
            runtime.target = None
            runtime.candidate = None
            runtime.hold_since = None
        if session is None:
            return
        try:
            session.stop(terminate_session=self._terminate_session)
        except Exception:  # pragma: no cover - 替身/异常进程
            logger.debug("帧率采集：会话停止异常", exc_info=True)
        try:
            session.close()
        except Exception:  # pragma: no cover
            logger.debug("帧率采集：会话收口异常", exc_info=True)

    def _note_failure(self, runtime: _Runtime, code: Any, reason: str) -> None:
        """记一次失败：终态（权限类）直接停用；否则有界退避重试。"""
        runtime.failures += 1
        if game_fps.is_terminal_failure(code, reason) or runtime.failures >= self._max_failures:
            self._set_fatal(reason)
            return
        backoff = self._retry_backoff_seconds * (2 ** max(0, runtime.failures - 1))
        runtime.next_attempt_at = self._clock() + backoff

    def _set_fatal(self, reason: str) -> None:
        text = str(reason or "采集失败")
        with self._lock:
            if not self._fatal_reason:
                self._fatal_reason = text
                logger.warning("帧率采集：停止重试（%s）", text)

    # ------------------------------------------------------------ 数据泵与发布
    def _pump(self, runtime: _Runtime) -> None:
        """把本拍到达的行喂进窗口（表头按列名认，行按 pid 过滤）。"""
        session = runtime.session
        if session is None:
            return
        try:
            lines = session.drain_lines()
        except Exception:  # pragma: no cover
            logger.debug("帧率采集：取行异常", exc_info=True)
            return
        for line in lines:
            self._consume(runtime, line)

    def _consume(self, runtime: _Runtime, line: str) -> None:
        """一行输出 → 窗口样本。表头/行都按**列名**解析，错位的行直接丢。"""
        if runtime.columns is None:
            problem = game_fps.header_problem(line)
            if problem:
                runtime.problem = problem
                return
            columns = game_fps.parse_header(line)
            if columns is None:
                return
            runtime.columns = columns
            runtime.clock_column = game_fps.event_clock_column(columns)
            return
        row = game_fps.parse_frame_row(line, runtime.columns, runtime.clock_column)
        if row is None:
            return
        target = runtime.target
        if target is not None and row.pid != target.pid:
            # 不能混 pid：会话是按目标 pid 起的，出现别人的行只能是数据源出了问题
            # （PID 复用 / 多实例），宁可丢掉也不记到目标头上。
            return
        now = self._clock()
        if (runtime.clock_offset is None and runtime.clock_column
                and row.event_ms is not None):
            # 事件时钟 → 我们挂钟的**一次性**校准：两边的推进速率相同（
            # TimeInMs 是 (QPC - 录制起点) 换算的毫秒），所以校一次就够；
            # 之后每一步的"事件距现在多久"都由它算出，用来识别积压。
            runtime.clock_offset = now - row.event_ms / 1000.0
        runtime.window.add(row, now)

    def _check_backend(self, runtime: _Runtime, now: float) -> None:
        """后端退出处置（会话不按时间回收：看 :meth:`FpsSession.stop` 的收口链）。"""
        session = runtime.session
        if session is None:
            return
        try:
            code = session.poll()
        except Exception:  # pragma: no cover
            code = None
        if code is None:
            return
        stderr = ""
        try:
            stderr = session.stderr_tail()
        except Exception:  # pragma: no cover
            stderr = ""
        state, reason = game_fps.classify_backend_failure(code, stderr)
        self._close_session(runtime, drop_target=True)
        raw = " ".join(str(stderr or "").split())[:game_fps.MAX_REASON_CHARS]
        runtime.raw_error = raw
        if state == game_fps.STATE_OK:
            runtime.failures = 0
            runtime.raw_error = ""
            runtime.no_target_reason = "目标进程已退出采集"
            return
        runtime.no_target_reason = reason
        if raw:
            logger.info("帧率采集：后端 stderr 原文：%s", raw)
        self._note_failure(runtime, code, reason)

    def _publish(self, runtime: _Runtime, now: float, token: object) -> None:
        sample = self._build_sample(runtime, now)
        if (sample.state == runtime.last_state
                and now - runtime.last_publish_at < self._publish_interval):
            return
        runtime.last_state = sample.state
        runtime.last_publish_at = now
        self._publish_sample(sample, token)

    def _build_sample(self, runtime: _Runtime, now: float) -> FpsSample:
        """把当前运行时状态翻成一份读数（状态判定集中在这里）。"""
        with self._lock:
            generation = self._generation
            fatal = self._fatal_reason
        detail_extra: dict[str, Any] = {"mode": self._mode}
        if self._mode == "process" and self._process:
            detail_extra["process"] = self._process

        if fatal:
            return self._sample(None, game_fps.STATE_ERROR, now, runtime, generation,
                                reason=fatal, detail_extra=detail_extra)
        if runtime.problem:
            return self._sample(None, game_fps.STATE_ERROR, now, runtime, generation,
                                reason=runtime.problem, detail_extra=detail_extra)
        if runtime.session is None:
            reason = runtime.no_target_reason or "没有可采集的目标"
            return self._sample(None, game_fps.NO_TARGET_STATE, now, runtime, generation,
                                reason=reason, clear_target=True,
                                detail_extra=detail_extra)

        reading = runtime.window.reading()
        lag = self._event_lag(runtime, now, reading)
        if reading.newest_arrival_at is None:
            # 一帧都还没收到：计时起点算会话开始时刻，否则"会话起来了但一直没有帧"
            # 会永远停在"采集中"，用户看着一个不动的 `--` 无从判断。
            if now - runtime.session_started_at > self._stale_seconds:
                reason = (f"会话已进行 {now - runtime.session_started_at:.0f} 秒仍未收到"
                          " Present 帧（目标可能没有渲染或已被最小化）")
                return self._sample(None, game_fps.NO_TARGET_STATE, now, runtime,
                                    generation, reason=reason,
                                    detail_extra=self._clock_detail(runtime, detail_extra))
            return self._sample(None, game_fps.WARMING_STATE, now, runtime, generation,
                                detail_extra=self._clock_detail(runtime, detail_extra))
        if lag is not None and lag > self._stale_seconds:
            if runtime.clock_column:
                reason = (f"数据落后现实约 {lag:.1f} 秒（管道积压或目标已停止出帧）"
                          "——不作为此刻的实时读数")
            else:
                reason = (f"目标超过 {self._stale_seconds:g} 秒没有新的 Present 帧"
                          "（可能暂停、最小化或已退出）")
            return self._sample(None, game_fps.NO_TARGET_STATE, now, runtime, generation,
                                reason=reason,
                                detail_extra=self._clock_detail(runtime, detail_extra, lag))
        if reading.fps is None:
            return self._sample(None, game_fps.WARMING_STATE, now, runtime, generation,
                                detail_extra=self._clock_detail(runtime, detail_extra, lag))
        extra = {**detail_extra, "chains": len(reading.chains), "frames": reading.frames}
        if reading.fps > game_fps.MAX_FPS:
            # 段上的字段盒只有 4 位（显示成 "9999+"），真实值在这里留档。
            extra["fps_exact"] = round(reading.fps, 1)
        return self._sample(reading.fps, game_fps.STATE_OK, now, runtime, generation,
                            displayed=reading.displayed_fps,
                            detail_extra=self._clock_detail(runtime, extra, lag))

    def _event_lag(self, runtime: _Runtime, now: float, reading) -> float | None:
        """窗口里最新的一帧**距现在多久**（秒）——判断"这个读数是不是此刻的"。

        有事件时钟时用它：``lag = (现在 - 校准偏移) - 最新事件时刻``。这是"我们手上
        最新的帧，在现实里已经过去多久"，因此管道积压（读到的是几秒前的历史）与目标
        停出帧都会体现在同一个数上——两者的处置是一样的：不算实时读数。

        没有事件时钟时退回到达时刻（我们收到它的时间），只能判"目标是否停出帧"，
        判不出积压；这个降级会写进详情（``clock`` = ``arrival``），不静默。
        """
        if runtime.clock_column and runtime.clock_offset is not None \
                and reading.newest_event_ms is not None:
            return (now - runtime.clock_offset) - reading.newest_event_ms / 1000.0
        if reading.newest_arrival_at is None:
            return None
        return now - reading.newest_arrival_at

    def _clock_detail(self, runtime: _Runtime, extra: dict,
                      lag: float | None = None) -> dict:
        """把"用的是哪种时钟、落后多少"写进详情（可诊断，不静默降级）。"""
        merged = dict(extra)
        merged["clock"] = runtime.clock_column or "arrival"
        if lag is not None:
            merged["lag_ms"] = int(max(0.0, lag) * 1000)
        return merged

    def _sample(self, fps, state, now, runtime: _Runtime, generation: int, *,
                reason: str = "", displayed: float | None = None,
                clear_target: bool = False, detail_extra: dict | None = None) -> FpsSample:
        target = None if clear_target else runtime.target
        pid = target.pid if target is not None else None
        name = target.name if target is not None else ""
        extra = dict(detail_extra or {})
        if state == game_fps.STATE_ERROR and runtime.raw_error:
            # 卡片那行是给用户看的短句，官方原文放详情（有界单行，只挂错误态）。
            extra["backend_stderr"] = runtime.raw_error
        detail = self._detail(state=state, reason=reason, pid=pid, name=name, extra=extra)
        if displayed is not None:
            detail["displayed_fps"] = displayed
        return FpsSample(
            fps=fps, state=state, sampled_at=now,
            target_pid=pid, target_name=name, error_reason=reason,
            displayed_fps=displayed, generation=generation, detail=detail,
        )

    def _detail(self, *, state: str, reason: str = "", pid: int | None = None,
                name: str = "", extra: dict | None = None) -> dict:
        """卡片 B 区的行素材（键与 ``gpu_monitor`` 同一套）。"""
        label = f"{name} (pid {pid})" if pid else ""
        detail: dict[str, Any] = {
            "unit_full": game_fps.UNIT_FULL,
            "source": game_fps.SOURCE_TEXT,
            "detail": label,
        }
        if reason:
            detail["error_reason"] = reason
        if extra:
            detail.update({k: v for k, v in extra.items() if v not in (None, "")})
        return detail

    def _publish_sample(self, sample: FpsSample, token: object) -> bool:
        """发布一份读数；**只认当前代**（已停止/已换代的那一轮直接丢弃）。"""
        with self._lock:
            if self._token is not token:
                return False
            self._snapshot = sample
        if callable(self._on_sample):
            try:
                self._on_sample(sample)
            except Exception:  # pragma: no cover - 回调是宿主注入面，异常不外溢
                logger.debug("帧率采集：读数回调异常", exc_info=True)
        return True
