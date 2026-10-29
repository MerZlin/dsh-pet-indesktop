# -*- coding: utf-8 -*-
"""游戏帧率探针（零 Qt、只依赖标准库）：PresentMon 子进程 + 1 秒事件窗统计。

**口径**：主值是目标应用**自己的 Present 帧率**，取 PresentMon 默认指标集里的
``MsBetweenPresents``（相邻两次 Present 的间隔，毫秒）换算 ``fps = 1000 / mean(间隔)``。
上屏帧率（``MsBetweenDisplayChange``）是另一个口径，只作详情，不与主值相加、平均或
互相替代。窗口抓取帧数、定时器节拍、GPU 占用都不是帧率，本模块不收。

**为什么按事件时间开窗**：CSV 每一行自带这一帧在录制时间轴上的时刻（``TimeInMs``）
与帧间隔。读进程多慢、管道怎么积压，都不改变这些数字，所以窗口按事件时间滚动
约 :data:`WINDOW_SECONDS` 秒，而不是"最近 N 帧"——30fps 下最近 240 帧是 8 秒，
那不是"当前帧率"。

**多条 swapchain 不相加**：一个应用可能有主渲染链与若干辅助链。每条
``(pid, swapchain)`` 各记一本账，主值取窗口内**仍然活着且最忙**的那一条链；
把两条链的帧率加起来会得到现实中不存在的读数。

**不虚 0**：没有读数时 ``fps`` 恒为 ``None``，状态按 ``sampling`` / ``unknown`` /
``unsupported`` / ``error`` 如实呈现。``0`` 只会是"真的 0 帧"，不会用来冒充不可用。

**线程纪律**：一条守护线程（:data:`THREAD_NAME`）负责开会话、读行、发布快照；
每场会话另有两条读线程（stdout / stderr）。``stop()`` 只取消、不 join；收口在
worker 里做（有界等退 → terminate → kill），所以 GUI 线程不会被采集链路吊住。
``shutdown(wait_seconds)`` 是**退出路径**：取消后最多等这么久让 worker 把官方终止
命令发出去，别让守护线程和解释器抢着收尾。

**目标生命周期**：目标正常退出（``--terminate_on_proc_exit`` → 退出码 0）会被记成
``target_gone``，同一个死 pid 不再重开会话（只有 ``set_target(新 pid)`` 解除）；
换目标要过 ``MIN_SESSION_SECONDS`` 与会话去抖，焦点抖动不会每秒开关一场 ETW 会话。

**开会话前先打扫**：进程内第一次真正起采集时，守护线程跑一次 ``logman query -ets``，
只停属主进程已退出的 ``dsh-pet-fps-*`` 残留会话——被强杀的进程走不到官方终止命令，
残留攒满后新会话会报 ``error 1450``。读不到、起不来只记原因，绝不编数字。

**一次只开一场会话**：进程级单例宿主 :func:`shared_probe` 让多宠共用同一个探针；
:meth:`FpsProbe.start` 幂等，已有活动会话时直接复用。
"""

from __future__ import annotations

import collections
import csv
import ctypes
import itertools
import logging
import math
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 后端（PresentMon）
#: 随 Windows 包分发的官方独立控制台程序文件名（升级必须同时改文件名与 SHA256）。
BACKEND_EXE_NAME = "PresentMon-2.6.0-x64.exe"
#: 后端在源码树 / 冻结包里的相对目录。
BACKEND_RELATIVE_DIR = ("resources", "presentmon")
#: 官方 v2.6.0 release 资产摘要（构建期由 ``scripts/verify_presentmon.py`` 校验；
#: 运行期不读 980KB 去算摘要，但常量留在这里作为单一事实）。
REQUIRED_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
#: 后端名称与呈现来源（卡片/详情共用）。
BACKEND_LABEL = "PresentMon 2.6.0"
SOURCE_TEXT = f"{BACKEND_LABEL} · MsBetweenPresents（应用 Present 帧率）"
UNIT_FULL = "游戏应用的 Present 帧率（相邻两次 Present 的间隔）"
#: 非 Windows 的能力说明（不冒称三端都有 FPS）。
PLATFORM_UNSUPPORTED_REASON = (
    "游戏帧率只在 Windows 上可用：数据源是 ETW 上的显卡 Present 事件"
)
ARM64_UNSUPPORTED_REASON = (
    "Windows ARM64 未验证：随包的 PresentMon 只有 x64 版本，暂无 ARM64 后端"
)
OTHER_ARCH_UNSUPPORTED_REASON = (
    "本机架构 {arch} 与随包的 x64 采集程序不匹配（只分发 x64）"
)
_X64_ARCHITECTURES = frozenset({"amd64", "x86_64", "x64", "em64t"})
_ARM64_ARCHITECTURES = frozenset({"arm64", "aarch64"})

#: 采集命令行的固定参数。逐条理由见 :func:`build_capture_args`。
CAPTURE_FLAGS = (
    "--output_stdout",
    "--no_console_stats",
    "--no_track_input",
    "--no_track_gpu",
    "--terminate_on_proc_exit",
)
#: **刻意禁用**的参数（每条都是纪律，不是遗漏）：
#: ``--stop_existing_session`` 会抢占别人的采集会话；``--restart_as_admin`` 是静默提权；
#: ``--v1_metrics`` / ``--v2_metrics`` 的口径里根本没有 ``MsBetweenPresents``
#: （v2 的帧间隔叫 ``FrameTime``），传了就永远解析不到主值。
FORBIDDEN_ARGS = (
    "--stop_existing_session",
    "--restart_as_admin",
    "--v1_metrics",
    "--v2_metrics",
)
SESSION_NAME_PREFIX = "dsh-pet-fps-"
MAX_SESSION_NAME_CHARS = 64
#: PresentMon 退出码：6 = 采集会话启动失败（权限不足最常见），7 = 终止命令没找到
#: 同名会话（对我们意味着"本来就已经停了"，不是错误）。
EXIT_START_FAILED = 6
EXIT_NO_SESSIONS = 7
_session_seq = itertools.count(1)

# ---------------------------------------------------------------- 状态词表
STATE_OK = "ok"
STATE_SAMPLING = "sampling"
STATE_UNKNOWN = "unknown"
STATE_UNSUPPORTED = "unsupported"
STATE_ERROR = "error"
STATES = frozenset(
    {STATE_OK, STATE_SAMPLING, STATE_UNKNOWN, STATE_UNSUPPORTED, STATE_ERROR}
)

# ---------------------------------------------------------------- 读数的界
#: 呈现层盒宽的表示上限：超过不夹紧冒充，而是显示 ``9999+``（"至少这么多"），
#: 真实值仍留在 ``FpsSample.fps`` 与详情里。
MAX_FPS = 9999.0
MAX_TEXT = "9999+"
#: 无读数时的占位文案（与灵动岛契约同一口径）。
UNKNOWN_TEXT = "--"
#: 1 秒事件窗：读数只由帧自带的事件时间决定。
WINDOW_SECONDS = 1.0
#: 报出读数需要至少两个样本，且它们的事件时间跨度 ≥ 0.1 秒。用跨度而不是固定帧数，
#: 是因为定死"≥8 帧"会让 8fps 以下的应用永远报不出读数，而 5fps 是真实且可测的。
MIN_FRAMES = 2
MIN_SPAN_SECONDS = 0.1
#: 每条链在窗口里的样本上限（1 秒窗在上万 fps 时会攒下巨量样本）。
MAX_CHAIN_SAMPLES = 2048
#: 同时跟踪的 ``(pid, swapchain)`` 条数上限（超出淘汰最久未更新的）。
MAX_CHAINS = 8

#: 单行 CSV 的字符上限：超长行整行丢弃，绝不截断后当数据解析。
MAX_LINE_CHARS = 4096
MAX_COLUMNS = 128
MAX_APPLICATION_CHARS = 64
MAX_SWAPCHAIN_CHARS = 32
MAX_REASON_CHARS = 120
MAX_STDERR_TEXT_CHARS = 400

# ---------------------------------------------------------------- CSV 列（官方契约）
#: 缺任何一列都无法给出可信读数：前三列是"这一帧属于谁、哪条链"，最后是帧率本身。
REQUIRED_COLUMNS = ("Application", "ProcessID", "SwapChainAddress", "MsBetweenPresents")
PRESENT_INTERVAL_COLUMN = "MsBetweenPresents"
#: 上屏时长列：上一帧在屏幕上停留的时长；``NA`` 表示这一帧没有上屏。
DISPLAYED_COLUMN = "MsBetweenDisplayChange"
#: 事件时钟列（按优先级）：流内单调的"距录制开始的毫秒数"。
EVENT_CLOCK_COLUMNS = ("TimeInMs", "CPUStartTimeInMs")
_HEADER_SHAPE = ("Application", "ProcessID")

# ---------------------------------------------------------------- 生命周期
#: 目标变化后稳定这么久才换会话（避免 Alt-Tab 掠过一堆窗口时反复重开会话）。
TARGET_STABLE_SECONDS = 0.75
#: 一场会话至少活这么久才允许因换目标换掉它。焦点在「游戏 ↔ 桌宠/浏览器」之间抖动
#: 时，换目标只会把一场 ETW 会话每秒开关一次（每次都是子进程 + 终止命令）。
MIN_SESSION_SECONDS = 3.0
#: 新目标要稳定这么久才换会话（与 :data:`TARGET_STABLE_SECONDS` 取较大者）。
TARGET_CHANGE_DEBOUNCE = 2.0
#: 超过这么久没有新的 Present 帧、或数据落后现实这么久 → 读数过期（不冒称实时）。
STALE_SECONDS = 3.0
#: 主循环节拍（10Hz 足够及时发现子进程退出与目标变化）。
POLL_SECONDS = 0.1
#: 快照发布节拍（约 4Hz）：读数变化比这快没有意义。
PUBLISH_INTERVAL_SECONDS = 0.25
MAX_CONSECUTIVE_FAILURES = 3
RETRY_BACKOFF_SECONDS = 2.0
STOP_TIMEOUT_SECONDS = 3.0
TERMINATE_TIMEOUT_SECONDS = 3.0
#: 退出收口的外层 join 上限：盖住一场会话自己的最坏收口。
EXIT_JOIN_TIMEOUT_SECONDS = TERMINATE_TIMEOUT_SECONDS + STOP_TIMEOUT_SECONDS + 1.0
MAX_BUFFERED_LINES = 4096
MAX_STDERR_LINES = 20
THREAD_NAME = "petcore-fps-probe"


# ---------------------------------------------------------------- 平台与路径
def machine_architecture() -> str:
    """本机**内核**架构的小写名（取不到返回空串）；纯函数、零子进程。"""
    if sys.platform != "win32":
        return ""
    native = (
        os.environ.get("PROCESSOR_ARCHITEW6432")
        or os.environ.get("PROCESSOR_ARCHITECTURE")
        or ""
    )
    return native.strip().lower()


def platform_problem() -> str:
    """平台/架构闸门不可用的人话原因；可用返回空串。

    读不出架构时不据此降级——只有明确看出 ARM64 或其它架构才说"没有这个能力"。
    """
    if sys.platform != "win32":
        return PLATFORM_UNSUPPORTED_REASON
    arch = machine_architecture()
    if not arch or arch in _X64_ARCHITECTURES:
        return ""
    if arch in _ARM64_ARCHITECTURES:
        return ARM64_UNSUPPORTED_REASON
    return OTHER_ARCH_UNSUPPORTED_REASON.format(arch=arch)


def platform_supported() -> bool:
    """本机有没有这条采集能力；每次现算（测试要按平台分支打桩）。"""
    return platform_problem() == ""


#: 模块函数别名：``FpsProbe.__init__`` 的 ``platform_supported`` 参数会遮蔽同名函数。
_default_platform_supported = platform_supported


#: 源码树的仓库根：导入时解析一次（``Path(__file__).resolve()`` 每次约 0.2ms，
#: 而 ``_blocked()`` 每拍都要问路径）。
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def repo_root() -> pathlib.Path:
    """源码树的仓库根（本文件在 ``<root>/petcore/platform/`` 下）。"""
    return _REPO_ROOT


def backend_path(
    *, frozen: bool, bundle_root: Any = None
) -> pathlib.Path | None:
    """内置采集程序的预期路径；无法定位时返回 ``None``。

    冻结包取 ``sys._MEIPASS/resources/presentmon/``（取不到时用 ``bundle_root``）；
    源码运行取 ``bundle_root`` 或仓库根。**绝不做 PATH 查找**：同名程序在 PATH 上
    有别的版本时，采到的就不是校验过的那个二进制。
    """
    base = getattr(sys, "_MEIPASS", "") if frozen else (bundle_root or repo_root())
    if not base:
        base = bundle_root
    if not base:
        return None
    return pathlib.Path(base).joinpath(*BACKEND_RELATIVE_DIR, BACKEND_EXE_NAME)


def backend_problem(
    *, frozen: bool = False, bundle_root: Any = None, path: Any = None
) -> str:
    """内置后端不可用的人话原因；可用返回空串。

    目录冒充可执行文件同样算不可用（含糊放过只会把失败推到子进程那一层）。
    """
    candidate = pathlib.Path(path) if path is not None else backend_path(
        frozen=frozen, bundle_root=bundle_root
    )
    if candidate is None:
        return (
            f"缺少内置采集程序 {BACKEND_EXE_NAME}"
            "（冻结包没有 sys._MEIPASS，无法定位内置资源目录）"
        )
    if candidate.is_file():
        return ""
    return f"缺少内置采集程序 {BACKEND_EXE_NAME}（预期路径：{candidate}）"


def backend_state(
    *, frozen: bool = False, bundle_root: Any = None, path: Any = None
) -> tuple[str, str]:
    """后端可用性 → ``(状态, 原因)``；可用时返回 ``("", "")``。"""
    problem = platform_problem() or backend_problem(
        frozen=frozen, bundle_root=bundle_root, path=path
    )
    if problem:
        return STATE_UNSUPPORTED, problem
    return "", ""


def new_session_name() -> str:
    """每次采集唯一的 ETW 会话名（``dsh-pet-fps-<pid>-<seq>-<rand>``）。

    唯一名让我们只可能启动/终止自己那一场会话，不会去抢别人的采集资源。
    """
    return "{}{}-{}-{}".format(
        SESSION_NAME_PREFIX, os.getpid(), next(_session_seq), secrets.token_hex(4)
    )


def build_capture_args(
    *, session_name: str, pid: int | None = None, process_name: str = ""
) -> list[str]:
    """采集命令行。只接受 PID 或进程名之一（没有目标的采集没有意义）。

    不出现 ``--stop_existing_session`` / ``--restart_as_admin`` /
    ``--v1_metrics`` / ``--v2_metrics``，理由见 :data:`FORBIDDEN_ARGS`。
    ``--no_track_display`` 不能加：那会把上屏时长列一起删掉。
    ``--terminate_on_proc_exit`` 让目标退出时它自己收尾。
    """
    name = str(session_name or "").strip()
    if not name:
        raise ValueError("采集会话名不能为空")
    if len(name) > MAX_SESSION_NAME_CHARS:
        raise ValueError(f"采集会话名过长（上限 {MAX_SESSION_NAME_CHARS}）：{name!r}")
    args = ["--session_name", name]
    args.extend(CAPTURE_FLAGS)
    if pid is not None:
        args += ["--process_id", str(int(pid))]
    elif str(process_name or "").strip():
        args += ["--process_name", str(process_name).strip()]
    else:
        raise ValueError("采集命令行必须给 --process_id 或 --process_name")
    return args


def build_terminate_args(session_name: str) -> list[str]:
    """停止**我们自己那一场**会话的命令行（官方退出方式）。

    不用 CTRL_BREAK / CTRL_C：采集进程以 ``CREATE_NO_WINDOW`` 拉起（没有可见控制台），
    信号能不能送达取决于进程组与控制台的绑定关系；``terminate()`` 是硬杀，会把 ETW
    会话留在系统里。``--terminate_existing_session`` 指名停掉那一场，进程随后自行退出。
    """
    name = str(session_name or "").strip()
    if not name:
        raise ValueError("终止会话名不能为空")
    return ["--session_name", name, "--terminate_existing_session"]


# ---------------------------------------------------------------- 失败归类
def _bounded_reason(text: Any) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:MAX_REASON_CHARS]


def _normal_exit(exit_code: Any) -> bool:
    """采集进程是不是**正常结束**（退出码 0 = ``--terminate_on_proc_exit`` 下目标退出）。"""
    try:
        return int(exit_code) == 0
    except (TypeError, ValueError):
        return False


def is_terminal_failure(exit_code: Any, stderr_text: str = "") -> bool:
    """这个失败要不要停止重试：权限类失败在这台机器上是稳定状态，不是抖动。"""
    text = str(stderr_text or "").lower()
    if "access denied" in text or "performance log users" in text:
        return True
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return False
    return code == EXIT_START_FAILED


def classify_failure(exit_code: Any, stderr_text: str = "") -> tuple[str, str]:
    """采集进程的收尾 → ``(状态, 人话原因)``。

    - 权限不足：``error`` + 官方原文关键词（用户拿它就能查到该加哪个组）；
    - 退出码 7（终止命令没找到会话）：``ok``，那是"本来就已经停了"；
    - 退出码 0：``ok``（``--terminate_on_proc_exit`` 下目标退出就是这条路径）；
    - 其它非零：``error`` + 退出码 + stderr 末行。
    """
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = -1
    text = str(stderr_text or "")
    low = text.lower()
    if "access denied" in low or "performance log users" in low:
        return STATE_ERROR, _bounded_reason(
            'PresentMon 权限不足：access denied —— 需要管理员权限或 '
            '"Performance Log Users" 组成员'
        )
    if "no existing sessions found" in low or code == EXIT_NO_SESSIONS:
        return STATE_OK, ""
    if code == 0:
        return STATE_OK, ""
    tail = ""
    for raw in reversed(text.splitlines()):
        if raw.strip():
            tail = raw.strip()
            break
    if tail:
        return STATE_ERROR, _bounded_reason(
            f"采集进程异常退出（退出码 {code}）：{tail}"
        )
    return STATE_ERROR, _bounded_reason(
        f"采集进程异常退出（退出码 {code}），且没有输出诊断信息"
    )


# ---------------------------------------------------------------- CSV 解析（纯函数）
def split_csv_line(line: Any) -> list[str]:
    """一行 CSV → 字段列表；空行 / 超长行 / 超宽行返回 ``[]``。

    用 ``csv`` 模块而不是 ``split(",")``：进程名里带逗号时硬切会让后面所有列错位。
    """
    if not isinstance(line, str):
        return []
    text = line.strip("\ufeff").rstrip("\r\n")
    if not text.strip():
        return []
    if len(text) > MAX_LINE_CHARS:
        return []
    try:
        fields = next(csv.reader([text]))
    except (csv.Error, StopIteration):
        return []
    if len(fields) > MAX_COLUMNS:
        return []
    return fields


def parse_header(line: Any) -> dict[str, int] | None:
    """CSV 表头 → ``{列名: 下标}``；不是表头（或列不全）返回 ``None``。

    按列名认表头、不按行号：真机 stdout 前面可能有 ``Started recording`` 之类的
    通知行，按"第 0 行是表头"解析会一路错位。
    """
    fields = split_csv_line(line)
    if not fields:
        return None
    names = [f.strip() for f in fields]
    if any(name not in names for name in REQUIRED_COLUMNS):
        return None
    return {name: index for index, name in enumerate(names)}


def event_clock_column(columns: Mapping[str, int] | None) -> str:
    """表头里可用的事件时钟列名（按优先级取第一个）；没有返回空串。"""
    if not columns:
        return ""
    for name in EVENT_CLOCK_COLUMNS:
        if name in columns:
            return name
    return ""


def header_problem(line: Any) -> str:
    """表头形态但缺必需列 → 人话原因；不是表头（或完好）返回空串。

    列名变了（后端升级 / 用错指标模式）必须能诊断成"表头缺列"，而不是表现为
    "采集中一直没有帧"——后者的排查方向完全不同。
    """
    fields = split_csv_line(line)
    if not fields:
        return ""
    names = [f.strip() for f in fields]
    if any(shape not in names for shape in _HEADER_SHAPE):
        return ""
    missing = [name for name in REQUIRED_COLUMNS if name not in names]
    if not missing:
        return ""
    hint = ""
    if "FrameTime" in names:
        hint = (
            "；表头看着像 --v2_metrics（v2 的帧间隔叫 FrameTime），"
            "我们用的是默认指标集"
        )
    return _bounded_reason(f"采集输出表头缺少必需列：{'、'.join(missing)}{hint}")


def _bounded_ms(value: Any) -> float | None:
    """毫秒读数 → 有限且 > 0 的浮点；``NA`` / 0 / 负数 / NaN / inf 一律 ``None``。

    ``NA`` 是官方语义（该帧没有上屏），必须与 0 分开：0 毫秒的间隔会换算成无穷大
    帧率，把它当数据就是把"没有数据"渲染成了"帧率爆表"。
    """
    text = "" if value is None else str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _event_ms(value: Any) -> float | None:
    """事件时钟读数（录制时间轴上的毫秒）→ 有限浮点；``NA`` / 坏数 → ``None``。

    与帧间隔不同，**0 是合法值**（录制起点附近），负值也保留：它只是坐标，
    不参与速率计算。
    """
    text = "" if value is None else str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class FrameEvent:
    """一帧的可用字段（PresentMon 一行 CSV 的必要部分）。

    ``time_ms`` 是这一帧在录制时间轴上的位置；取不到时为 ``None``（窗口退回
    到达时钟）。``displayed_ms`` 是上一帧的上屏时长，只作详情。
    """

    time_ms: float | None
    interval_ms: float | None
    pid: int
    swapchain: str
    displayed_ms: float | None = None


def parse_frame_event(
    line: Any, columns: Mapping[str, int] | None, clock_column: str = ""
) -> FrameEvent | None:
    """一行数据 → :class:`FrameEvent`；不是数据行返回 ``None``（噪声/坏行）。

    坏行不抛异常：真机 stdout 里混着通知行、空行、被截断的行都发生过，
    一行坏数据不该终止整场采集。
    """
    if not columns:
        return None
    fields = split_csv_line(line)
    if len(fields) < len(columns):
        return None

    def value(name: str) -> str | None:
        index = columns.get(name)
        if index is None or index >= len(fields):
            return None
        return fields[index]

    pid_text = (value("ProcessID") or "").strip()
    try:
        pid = int(pid_text)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    application = (value("Application") or "").strip()[:MAX_APPLICATION_CHARS]
    if not application:
        return None
    swapchain = (value("SwapChainAddress") or "").strip()
    if not swapchain or len(swapchain) > MAX_SWAPCHAIN_CHARS:
        return None
    return FrameEvent(
        time_ms=_event_ms(value(clock_column)) if clock_column else None,
        interval_ms=_bounded_ms(value(PRESENT_INTERVAL_COLUMN)),
        pid=pid,
        swapchain=swapchain,
        displayed_ms=_bounded_ms(value(DISPLAYED_COLUMN)),
    )


@dataclass(frozen=True)
class ChainReading:
    """一条 swapchain 的读数。"""

    pid: int
    swapchain: str
    fps: float | None
    displayed_fps: float | None
    frames: int
    last_event_ms: float | None = None
    last_arrival_at: float | None = None


@dataclass(frozen=True)
class FpsReading:
    """一个窗口整体的读数：主值 + 各链明细。"""

    fps: float | None
    displayed_fps: float | None
    pid: int | None
    frames: int
    chains: tuple[ChainReading, ...] = ()
    newest_event_ms: float | None = None
    newest_arrival_at: float | None = None
    clock: str = "arrival"


class _Chain:
    """单条链的定长窗口：``(时刻, 帧间隔, 上屏时长)``，按事件时间滚动。"""

    __slots__ = (
        "samples",
        "max_samples",
        "window_ms",
        "last_event_ms",
        "last_arrival_at",
    )

    def __init__(self, max_samples: int, window_ms: float):
        self.samples: collections.deque = collections.deque(maxlen=max_samples)
        self.max_samples = int(max_samples)
        self.window_ms = float(window_ms)
        self.last_event_ms: float | None = None
        self.last_arrival_at: float | None = None

    def add(
        self,
        t_ms: float,
        interval: float | None,
        displayed: float | None,
        arrival: float,
    ) -> None:
        self.samples.append((float(t_ms), interval, displayed))
        self.last_event_ms = float(t_ms)
        self.last_arrival_at = float(arrival)

    def prune(self, newest_ms: float) -> None:
        """丢掉落在窗口之外的老样本（``newest_ms`` 是全局最新的事件时刻）。"""
        cutoff = newest_ms - self.window_ms
        samples = self.samples
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def _values(self, index: int) -> list[float]:
        return [sample[index] for sample in self.samples if sample[index] is not None]

    @staticmethod
    def _rate(values: list[float]) -> float | None:
        """样本 → 帧率（``1000 / 平均间隔``）；没有可用样本返回 ``None``。"""
        if not values:
            return None
        mean = math.fsum(values) / len(values)
        if mean <= 0:
            return None
        return 1000.0 / mean

    def span_ms(self) -> float:
        if not self.samples:
            return 0.0
        return self.samples[-1][0] - self.samples[0][0]

    def present_count(self) -> int:
        return len(self._values(1))

    def fps(self) -> float | None:
        return self._rate(self._values(1))

    def displayed_fps(self) -> float | None:
        return self._rate(self._values(2))


class FpsWindow:
    """按 ``(pid, swapchain)`` 分窗累积帧样本，给出主读数（**纯计算，无 syscall**）。"""

    def __init__(
        self,
        *,
        window_seconds: float = WINDOW_SECONDS,
        max_chains: int = MAX_CHAINS,
        max_samples: int = MAX_CHAIN_SAMPLES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._window_ms = max(1.0, float(window_seconds) * 1000.0)
        self._max_chains = max(1, int(max_chains))
        self._max_samples = max(2, int(max_samples))
        self._clock = clock
        self._chains: collections.OrderedDict[tuple[int, str], _Chain] = (
            collections.OrderedDict()
        )
        self._newest_ms: float | None = None
        self._clock_kind = "arrival"
        self.columns: dict[str, int] | None = None
        self.clock_column = ""
        self.problem = ""

    def reset(self) -> None:
        """清空：上一段窗口不许跨目标/跨会话残留。"""
        self._chains.clear()
        self._newest_ms = None
        self._clock_kind = "arrival"
        self.columns = None
        self.clock_column = ""
        self.problem = ""

    # ------------------------------------------------------------ 输入
    def add(self, item: Any) -> bool:
        """喂一条输入（``FrameEvent`` / ``(time, interval, pid, swapchain)`` / CSV 行）。"""
        if isinstance(item, FrameEvent):
            return self.add_event(item)
        if isinstance(item, str):
            return self.add_line(item)
        if isinstance(item, (tuple, list)) and len(item) >= 4:
            try:
                pid = int(item[2])
                swapchain = str(item[3])
            except (TypeError, ValueError):
                return False
            displayed = item[4] if len(item) > 4 else None
            return self.add_event(
                FrameEvent(
                    time_ms=_event_ms(item[0]),
                    interval_ms=_bounded_ms(item[1]),
                    pid=pid,
                    swapchain=swapchain,
                    displayed_ms=_bounded_ms(displayed),
                )
            )
        return False

    def feed_line(self, line: Any) -> FrameEvent | None:
        """一行输出 → 事件（**不入窗**）；表头/噪声返回 ``None``。

        表头与缺列诊断在这里更新，调用方可以先按 pid 过滤再 :meth:`add_event`。
        """
        if self.columns is None:
            problem = header_problem(line)
            if problem:
                self.problem = problem
                return None
            columns = parse_header(line)
            if columns is None:
                return None
            self.columns = columns
            self.clock_column = event_clock_column(columns)
            return None
        return parse_frame_event(line, self.columns, self.clock_column)

    def add_line(self, line: Any) -> bool:
        event = self.feed_line(line)
        if event is None:
            return False
        return self.add_event(event)

    def add_event(self, event: FrameEvent | None, *, arrival: float | None = None) -> bool:
        """喂一帧；返回是否记下了至少一个可用样本。

        ``arrival`` 是"我们收到这一帧"的挂钟秒，缺省取窗口自己的时钟；没有事件
        时钟时窗口坐标就换成它（调用方一旦发现没有事件时钟就整场都用到达时钟）。
        """
        if event is None:
            return False
        try:
            pid = int(event.pid)
        except (TypeError, ValueError):
            return False
        swapchain = str(event.swapchain or "")
        if pid <= 0 or not swapchain:
            return False
        interval = _bounded_ms(event.interval_ms)
        displayed = _bounded_ms(event.displayed_ms)
        if interval is None and displayed is None:
            return False
        now = float(self._clock() if arrival is None else arrival)
        event_ms = _event_ms(event.time_ms)
        if event_ms is not None:
            t_ms = float(event_ms)
            self._clock_kind = "event"
        else:
            t_ms = now * 1000.0
        self._newest_ms = t_ms if self._newest_ms is None else max(self._newest_ms, t_ms)
        key = (pid, swapchain)
        chain = self._chains.get(key)
        if chain is None:
            chain = _Chain(self._max_samples, self._window_ms)
            self._chains[key] = chain
        else:
            self._chains.move_to_end(key)
        chain.add(t_ms, interval, displayed, now)
        newest = self._newest_ms if self._newest_ms is not None else t_ms
        for item in self._chains.values():
            item.prune(newest)
        self._evict_over_limit()
        return True

    def _evict_over_limit(self) -> None:
        """超出链数上限时淘汰**最久未更新**的那条（不淘汰刚进来的那条）。"""
        while len(self._chains) > self._max_chains:
            oldest_key = min(
                self._chains,
                key=lambda key: self._chains[key].last_event_ms or 0.0,
            )
            self._chains.pop(oldest_key, None)

    # ------------------------------------------------------------ 读数
    def reading(self) -> FpsReading:
        """当前主读数 + 各链明细。

        主值只认窗口内还活着的链（:meth:`_Chain.prune` 已清掉过期样本），且要求
        样本数 ≥ :data:`MIN_FRAMES`、事件跨度 ≥ :data:`MIN_SPAN_SECONDS`；
        否则 ``fps`` 为 ``None``（按"采集中"呈现，不拿一两帧的抖动当读数）。
        """
        chains: list[ChainReading] = []
        for (pid, swapchain), chain in self._chains.items():
            covered = (
                chain.present_count() >= MIN_FRAMES
                and chain.span_ms() >= MIN_SPAN_SECONDS * 1000.0
            )
            fps = chain.fps() if covered else None
            chains.append(
                ChainReading(
                    pid=pid,
                    swapchain=swapchain,
                    fps=fps,
                    displayed_fps=chain.displayed_fps() if fps is not None else None,
                    frames=chain.present_count(),
                    last_event_ms=chain.last_event_ms,
                    last_arrival_at=chain.last_arrival_at,
                )
            )
        chains.sort(key=lambda c: (c.fps is None, -(c.fps or 0.0), -c.frames))
        eligible = [c for c in chains if c.fps is not None]
        main = eligible[0] if eligible else None
        arrivals = [c.last_arrival_at for c in chains if c.last_arrival_at is not None]
        return FpsReading(
            fps=main.fps if main is not None else None,
            displayed_fps=main.displayed_fps if main is not None else None,
            pid=main.pid if main is not None else None,
            frames=main.frames if main is not None else 0,
            chains=tuple(chains),
            newest_event_ms=self._newest_ms,
            newest_arrival_at=max(arrivals) if arrivals else None,
            clock=self._clock_kind,
        )

    def fps(self) -> float | None:
        return self.reading().fps


def window_fps(
    items: Iterable[Any], *, window_seconds: float = WINDOW_SECONDS
) -> float | None:
    """一组帧输入 → 1 秒窗主读数；不够覆盖 / 空输入返回 ``None``。

    输入可以是 :class:`FrameEvent`、``(time_ms, ms_between_presents, pid,
    swapchain[, displayed_ms])`` 元组，或 PresentMon 的 CSV 行序列（首行须是表头）。
    """
    window = FpsWindow(window_seconds=window_seconds)
    for item in items:
        window.add(item)
    return window.fps()


def format_fps(value: Any) -> str:
    """帧率 → 段上的整数字段；无读数 / 非数字 / 负数 → ``"--"``。

    超出呈现盒宽时给 ``"9999+"``（"至少这么多"），不夹紧成 ``9999``：那是把一个
    更大的读数说成了具体值。真实数值仍在 ``FpsSample.fps`` 与详情里。
    """
    if value is None:
        return UNKNOWN_TEXT
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNKNOWN_TEXT
    if not math.isfinite(number) or number < 0:
        return UNKNOWN_TEXT
    if number > MAX_FPS:
        return f"{MAX_FPS:.0f}+"
    return f"{number:.0f}"


# ---------------------------------------------------------------- 读数快照
@dataclass(frozen=True)
class FpsSample:
    """一个发布周期的读数快照（GUI 线程只读它，不做任何 syscall）。

    ``fps`` 是**目标应用的 Present 帧率**，无读数时恒为 ``None``（呈现层画 ``--``），
    绝不伪报 0。``state`` 取 :data:`STATES`；``error_reason`` 是给用户看的一句原因。
    """

    pid: int | None = None
    fps: float | None = None
    displayed_fps: float | None = None
    state: str = STATE_UNKNOWN
    error_reason: str = ""
    target: str = ""
    backend: str = BACKEND_LABEL
    detail: dict = field(default_factory=dict)
    sampled_at: float = 0.0


# ---------------------------------------------------------------- 孤儿防护（Windows Job）
#: ``TerminateProcess`` 下父进程一行清理代码都不会跑，唯一还在工作的只有内核：
#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` 让采集进程随桌宠进程一起死（含被强杀）。
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_job_lock = threading.Lock()
_job_handle: int | None = None
_job_failed = False
_k32 = None


def _kernel32():
    global _k32
    if _k32 is not None:
        return _k32
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.SetInformationJobObject.restype = ctypes.c_int
    k32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    k32.AssignProcessToJobObject.restype = ctypes.c_int
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32 = k32
    return k32


def _ensure_job() -> int | None:
    """创建并配置单例 job（幂等，失败只告警一次；句柄永不关闭）。"""
    global _job_handle, _job_failed
    if os.name != "nt" or _job_failed:
        return _job_handle
    with _job_lock:
        if _job_handle is not None or _job_failed:
            return _job_handle
        try:
            k32 = _kernel32()
            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW 失败")
            info = _ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = _JOB_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(
                ctypes.c_void_p(handle),
                _JOB_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
            _job_handle = handle
            logger.debug("帧率探针：父进程退出时连带终止采集子进程（Job Object 就绪）")
        except Exception:  # noqa: BLE001 - 防护失败只降级，不影响采集
            _job_failed = True
            logger.warning("帧率探针：孤儿防护不可用（Job Object 创建失败）", exc_info=True)
    return _job_handle


def _adopt_into_job(proc: Any) -> bool:
    """把一个已创建的子进程挂进 kill-on-close job（幂等；非 Windows 无操作）。"""
    if os.name != "nt" or proc is None:
        return False
    handle = _ensure_job()
    if handle is None:
        return False
    try:
        pid = int(getattr(proc, "pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    child = None
    owned = False
    try:
        raw = getattr(proc, "_handle", None)
        if raw:
            child = ctypes.c_void_p(int(raw))
        else:
            child = ctypes.c_void_p(
                _kernel32().OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            )
            owned = True
            if not child:
                return False
        if not _kernel32().AssignProcessToJobObject(ctypes.c_void_p(handle), child):
            # 进程已退出时 Assign 会失败，这是最常见的正常情形。
            return False
        return True
    except Exception:  # noqa: BLE001 - 纯内核句柄操作失败不抛给调用方
        return False
    finally:
        if owned and child:
            try:
                _kernel32().CloseHandle(child)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 残留会话清扫（Windows）
#: 清残留用系统自带的 ``logman``：不新增依赖、不提权、不装服务。**只碰我们自己的
#: 命名空间**（``dsh-pet-fps-*``）且有明确证据属于已退出进程的会话——别人的采集
#: 一行都不碰，判活拿不到结论时按"活着"处理。
LOGMAN_EXE = "logman.exe"
SWEEP_QUERY_TIMEOUT_SECONDS = 8.0
SWEEP_STOP_TIMEOUT_SECONDS = 5.0
SWEEP_THREAD_NAME = "petcore-fps-sweep"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def sweep_supported() -> bool:
    """这台机器有没有 ETW（残留会话只可能在 Windows 上存在）。"""
    return os.name == "nt"


def parse_logman_sessions(text: Any) -> list[str]:
    """``logman query -ets`` 输出 → 我们命名空间里的会话名（去重、保序）。

    按**行首 token** 认，不依赖表头语言（中文 Windows 的表头是「数据收集器集」）：
    我们自己起的会话名固定以 :data:`SESSION_NAME_PREFIX` 开头，别的会话不会命中。
    """
    names: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        token = line.split()[0].strip('"')
        if token.startswith(SESSION_NAME_PREFIX) and token not in names:
            names.append(token)
    return names


def session_owner_pid(name: Any) -> int | None:
    """``dsh-pet-fps-<pid>-<seq>-<rand>`` → 起它的进程 pid；解析不了返回 ``None``。"""
    text = str(name or "")
    if not text.startswith(SESSION_NAME_PREFIX):
        return None
    head = text[len(SESSION_NAME_PREFIX):].split("-", 1)[0]
    try:
        pid = int(head)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_alive(pid: int) -> bool:
    """进程是否还活着；**取不到结论按"活着"处理**（宁可漏清，不误停别人的会话）。"""
    if not sweep_supported():
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except Exception:  # noqa: BLE001 - 接口/权限问题一律保守
        return True
    if not handle:
        # 打不开：别人的进程（ACCESS_DENIED）可能活着；其余（含 pid 不存在）算退出。
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def _run_logman(args: list[str], timeout: float):
    """跑一次 logman（有界；隐藏控制台窗口）。"""
    return subprocess.run(
        [LOGMAN_EXE, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_creation_flags(),
        check=False,
    )


def sweep_residual_sessions(*, runner=None, alive=None, own_pid=None, log=None) -> int | None:
    """停掉属主进程已退出的 ``dsh-pet-fps-*`` 会话；返回**实际停掉的条数**。

    返回 ``None`` = 这次没查成（找不到 logman / 调用失败 / query 退出码非 0），
    调用方据此决定下次要不要再试；其余情况一律返回真实条数（读不到就 0，不编数字）。
    会起子进程并阻塞，调用方必须放到后台线程。
    """
    runner_fn = runner or (lambda args, timeout: _run_logman(args, timeout))
    alive_fn = alive or pid_alive
    log_fn = log or logger
    me = os.getpid() if own_pid is None else int(own_pid)
    try:
        result = runner_fn(["query", "-ets"], SWEEP_QUERY_TIMEOUT_SECONDS)
    except FileNotFoundError:
        log_fn.info("帧率探针：跳过残留会话清扫（找不到 %s）", LOGMAN_EXE)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        log_fn.info(
            "帧率探针：跳过残留会话清扫（%s 调用失败：%s）",
            LOGMAN_EXE,
            type(exc).__name__,
        )
        return None
    code = getattr(result, "returncode", 0)
    if code not in (0, None):
        log_fn.info("帧率探针：跳过残留会话清扫（%s query 退出码 %s）", LOGMAN_EXE, code)
        return None
    stopped = 0
    for name in parse_logman_sessions(getattr(result, "stdout", "")):
        owner = session_owner_pid(name)
        if owner is None or owner == me:
            continue
        try:
            if alive_fn(owner):
                continue
        except Exception:  # noqa: BLE001 - 判活失败按活着处理
            continue
        try:
            runner_fn(["stop", name, "-ets"], SWEEP_STOP_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            log_fn.info("帧率探针：残留会话 %s 停不掉（%s）", name, type(exc).__name__)
            continue
        stopped += 1
    if stopped:
        log_fn.info("帧率探针：清掉 %d 个残留采集会话", stopped)
    return stopped


_sweep_lock = threading.Lock()
_sweep_started = False


def reset_residual_sweep() -> None:
    """忘掉「本进程已经扫过」（退出钩子/测试用；正式路径每进程只扫一次）。"""
    global _sweep_started
    with _sweep_lock:
        _sweep_started = False


def start_residual_sweep_once(*, sweeper=None) -> bool:
    """本进程第一次真正起采集时扫一次残留会话；返回这次是否真的起了线程。

    **守护线程 + 每进程一次**：打扫是顺手的事，不许阻塞起步、不许每场会话都扫。
    查询失败（``sweep()`` 返回 ``None``）时闸门重新打开，下一次真起采集再试——
    与主分支 ``sweep_stale_sessions`` 的「查询失败不关闸」同一口径。
    """
    global _sweep_started
    if not sweep_supported():
        return False
    with _sweep_lock:
        if _sweep_started:
            return False
        _sweep_started = True
    sweep = sweeper or sweep_residual_sessions

    def worker() -> None:
        global _sweep_started
        try:
            outcome = sweep()
        except Exception:  # noqa: BLE001 - 打扫失败不影响采集
            logger.debug("帧率探针：残留会话清扫异常", exc_info=True)
            outcome = None
        if outcome is None:
            with _sweep_lock:
                _sweep_started = False

    threading.Thread(target=worker, name=SWEEP_THREAD_NAME, daemon=True).start()
    return True


# ---------------------------------------------------------------- 会话（子进程）
def _creation_flags() -> int:
    """Windows：隐藏控制台窗口（缺了它每次开会话都闪一个终端）。"""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


class FpsSessionError(RuntimeError):
    """一场采集会话起不来（后端缺失 / Popen 失败）。"""


class FpsSession:
    """一场 PresentMon 采集会话：一个子进程 + 两条读线程 + 有界收口。

    公开契约（测试按它打桩）::

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
        popen: Any = None,
    ):
        self.pid = int(pid)
        self.target_name = str(name)
        self.backend = pathlib.Path(backend)
        self.session_name = str(session_name)
        self._stop_timeout = float(stop_timeout)
        self._terminate_timeout = float(terminate_timeout)
        self._clock = clock
        self._sleep = sleep
        self._popen = popen or subprocess.Popen
        self._proc: Any = None
        self._lines: collections.deque[str] = collections.deque(
            maxlen=MAX_BUFFERED_LINES
        )
        self._stderr: collections.deque[str] = collections.deque(
            maxlen=MAX_STDERR_LINES
        )
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._closing = threading.Event()

    @property
    def argv(self) -> list[str]:
        """本场会话的采集命令行（证据与测试用）。"""
        return build_capture_args(session_name=self.session_name, pid=self.pid)

    @property
    def process(self) -> Any:
        return self._proc

    def start(self) -> None:
        """起采集进程并挂上读线程。后端不可用/进程起不来都抛 :class:`FpsSessionError`。"""
        problem = backend_problem(path=self.backend)
        if problem:
            raise FpsSessionError(problem)
        argv = [str(self.backend)] + self.argv
        try:
            proc = self._popen(
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
        _adopt_into_job(proc)
        self._spawn_readers(proc)

    def _spawn_readers(self, proc: Any) -> None:
        self._closing.clear()
        self._threads = [
            self._new_thread(f"{THREAD_NAME}-stdout", self._read_stdout, proc.stdout),
            self._new_thread(f"{THREAD_NAME}-stderr", self._read_stderr, proc.stderr),
        ]
        for thread in self._threads:
            thread.start()

    @staticmethod
    def _new_thread(name: str, target: Callable[..., None], stream: Any):
        return threading.Thread(target=target, args=(stream,), daemon=True, name=name)

    def _read_stdout(self, stream: Any) -> None:
        """读 CSV 行：**超长行整行丢弃**，绝不截断后当数据解析。

        ``readline(MAX_LINE_CHARS)`` 保证单次读取有上界；返回长度封顶又没见到换行，
        就只能是一个超长行——丢掉它直到下一个换行（截断后的半个字段是错的列值）。
        """
        carry = ""
        dropping = False
        try:
            while not self._closing.is_set():
                chunk = stream.readline(MAX_LINE_CHARS)
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
                elif len(chunk) >= MAX_LINE_CHARS:
                    dropping = True
                else:
                    carry = line
            if carry:
                self._append_line(carry)
        except (ValueError, OSError):
            # 管道被关闭（进程退出 / 我们主动收口）：正常路径，不是错误。
            pass
        except Exception:  # noqa: BLE001 - 读线程异常不该外溢
            logger.debug("帧率探针：stdout 读取异常", exc_info=True)

    def _read_stderr(self, stream: Any) -> None:
        try:
            while not self._closing.is_set():
                line = stream.readline(MAX_STDERR_TEXT_CHARS)
                if not line:
                    break
                text = line.rstrip("\r\n").strip()
                if text:
                    with self._lock:
                        self._stderr.append(text[:MAX_STDERR_TEXT_CHARS])
        except (ValueError, OSError):
            pass
        except Exception:  # noqa: BLE001
            logger.debug("帧率探针：stderr 读取异常", exc_info=True)

    def _append_line(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def drain_lines(self) -> list[str]:
        """取走缓冲里的行（有界缓冲：拿不到的只有更老的那些帧）。"""
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

    def stop(
        self, timeout: float | None = None, *, terminate_session: bool = True
    ) -> None:
        """停止本场采集：官方终止命令 → 有界等它自己退 → terminate → kill。

        ``terminate_session=False``（关机/注销路径）跳过那条终止命令：关机窗口正是
        最不该再派生进程的时刻，只做有界硬停。
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
        argv = [str(self.backend)] + build_terminate_args(self.session_name)
        try:
            proc = self._popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except OSError:
            logger.debug("帧率探针：终止命令启动失败（继续按有界等待收口）", exc_info=True)
            return
        _adopt_into_job(proc)
        try:
            proc.communicate(timeout=self._terminate_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            logger.debug("帧率探针：终止命令等待异常", exc_info=True)

    def close(self) -> None:
        """收口读线程与进程（幂等；有界 join，卡死也不吊住调用方）。"""
        self._closing.set()
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - close 不抛
            logger.debug("帧率探针：会话收口时停止异常", exc_info=True)
        proc = self._proc
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        for thread in self._threads:
            thread.join(timeout=0.5)
        self._threads = []
        # 丢掉 Popen 引用：子进程句柄随对象释放（``stop`` 已经保证它不在跑），
        # 长命进程里反复换会话才不会攒下一串句柄。
        self._proc = None


# ---------------------------------------------------------------- 探针（生命周期）
@dataclass
class _Runtime:
    """一轮采集线程的私有状态（跨拍保留；停止/换代时随线程一起丢弃）。"""

    window: FpsWindow
    session: Any = None
    pid: int | None = None
    #: 本场会话开会话时的目标名（换目标去抖期间旧会话仍按旧名字呈现，不张冠李戴）。
    name: str = ""
    session_name: str = ""
    #: 已经应用过的"显式重试"代次（换目标 = 一次重试，见 ``FpsProbe.set_target``）。
    retry_epoch: int = 0
    session_started_at: float = 0.0
    clock_column: str = ""
    #: 事件时钟 → 挂钟的一次性校准：``offset = 收到它的时刻 - 事件秒``。
    clock_offset: float | None = None
    problem: str = ""
    raw_stderr: str = ""
    #: 后端进程的退出码（只在失败时留在详情里，便于对照官方退出码表）。
    exit_code: int | None = None
    notice: str = ""
    error: str = ""
    terminal: str = ""
    blocked: tuple[str, str] = ("", "")
    #: 目标进程已正常退出（``--terminate_on_proc_exit``）：不重开同一个死 pid。
    target_gone: bool = False
    #: 已退出的目标 pid（``set_target`` 换掉目标才解除）。
    gone_pid: int | None = None
    failures: int = 0
    next_attempt_at: float = 0.0
    last_publish_at: float = 0.0
    last_state: str = ""


class FpsProbe:
    """游戏帧率探针的对外入口：目标、会话、快照（调用方不碰子进程）。

    生命周期按 :class:`~petcore.platform.sampling.SamplingLoop` 的同一形状：
    ``start()`` / ``stop()`` / ``running()`` / ``wait_stopped()`` / ``snapshot()`` /
    ``poll()``。``stop()`` 只取消、不 join；收口在 worker 里完成。

    调用方按"段可见且未折叠"驱动：可见就 ``set_target(pid, name)`` + ``start()``，
    隐藏/折叠就 ``stop()``。``start()`` 幂等——已有活动会话时直接复用，不会开第二场。
    """

    def __init__(
        self,
        *,
        target_pid: int | None = None,
        target_name: str = "",
        frozen: bool | None = None,
        bundle_root: Any = None,
        backend: Any = None,
        platform_supported: Callable[[], bool] | None = None,
        session_factory: Callable[[int, pathlib.Path, str], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = POLL_SECONDS,
        publish_interval: float = PUBLISH_INTERVAL_SECONDS,
        stale_seconds: float = STALE_SECONDS,
        target_stable_seconds: float = TARGET_STABLE_SECONDS,
        min_session_seconds: float = MIN_SESSION_SECONDS,
        target_change_debounce: float = TARGET_CHANGE_DEBOUNCE,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
        retry_backoff_seconds: float = RETRY_BACKOFF_SECONDS,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
        terminate_timeout: float = TERMINATE_TIMEOUT_SECONDS,
        on_sample: Callable[[FpsSample], None] | None = None,
        sweep_starter: Callable[[], object] | None = None,
    ):
        self._clock = clock
        self._platform_supported_fn = platform_supported or _default_platform_supported
        self._frozen = (
            bool(getattr(sys, "_MEIPASS", "")) if frozen is None else bool(frozen)
        )
        self._bundle_root = bundle_root
        # 路径在构造时定一次：``_blocked()`` 每拍都要用，反复 resolve 不值当。
        self._backend_path = (
            pathlib.Path(backend)
            if backend is not None
            else backend_path(frozen=self._frozen, bundle_root=bundle_root)
        )
        self._session_factory = session_factory or self._default_session
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._publish_interval = max(0.0, float(publish_interval))
        self._stale_seconds = max(0.0, float(stale_seconds))
        self._target_stable_seconds = max(0.0, float(target_stable_seconds))
        self._min_session_seconds = max(0.0, float(min_session_seconds))
        self._target_change_debounce = max(0.0, float(target_change_debounce))
        self._max_failures = max(1, int(max_failures))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._stop_timeout = float(stop_timeout)
        self._terminate_timeout = float(terminate_timeout)
        self._on_sample = on_sample
        self._sweep_starter = sweep_starter or start_residual_sweep_once

        self._lock = threading.Lock()
        self._target_pid = self._normalize_pid(target_pid)
        self._target_name = str(target_name or "")
        self._target_requested_at: float | None = None
        #: 显式重试代次：换目标时 +1，worker 据此清掉终态错误与退避。
        self._retry_epoch = 0
        self._thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._token: object | None = None
        self._snapshot = self._initial_sample()

    # ------------------------------------------------------------ 构造现场
    @staticmethod
    def _normalize_pid(pid: Any) -> int | None:
        try:
            value = int(pid) if pid is not None else 0
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _default_session(
        self, pid: int, backend: pathlib.Path, session_name: str
    ) -> FpsSession:
        _, name = self._target()
        return FpsSession(
            pid=pid,
            name=name,
            backend=backend,
            session_name=session_name,
            stop_timeout=self._stop_timeout,
            terminate_timeout=self._terminate_timeout,
            clock=self._clock,
        )

    # ------------------------------------------------------------ 状态
    @property
    def backend(self) -> pathlib.Path | None:
        """本探针要用的采集程序路径（构造时按 frozen/bundle_root 解析一次）。

        冻结包缺 ``sys._MEIPASS`` 且没给 ``bundle_root`` 时为 ``None``——那是
        "无法定位"，不是"路径猜一个"。
        """
        return self._backend_path

    @property
    def target_pid(self) -> int | None:
        with self._lock:
            return self._target_pid

    @property
    def target_name(self) -> str:
        with self._lock:
            return self._target_name

    def _target(self) -> tuple[int | None, str]:
        with self._lock:
            return self._target_pid, self._target_name

    def _blocked(self) -> tuple[str, str]:
        """能力层面的阻塞原因 ``(状态, 原因)``；可用返回 ``("", "")``。"""
        if not self._platform_supported_fn():
            return STATE_UNSUPPORTED, platform_problem() or PLATFORM_UNSUPPORTED_REASON
        path = self.backend
        if path is None:
            # 冻结包没有 sys._MEIPASS：路径确实不可知，不能退回去查源码树。
            return STATE_UNSUPPORTED, (
                f"缺少内置采集程序 {BACKEND_EXE_NAME}"
                "（冻结包没有 sys._MEIPASS，无法定位内置资源目录）"
            )
        problem = backend_problem(path=path)
        if problem:
            return STATE_UNSUPPORTED, problem
        return "", ""

    def _label(self, pid: int | None, name: str) -> str:
        if pid is None:
            return ""
        return f"{name} (pid {pid})" if name else f"pid {pid}"

    def _make_sample(
        self,
        *,
        state: str,
        reason: str = "",
        fps: float | None = None,
        displayed: float | None = None,
        pid: int | None = None,
        name: str = "",
        extra: dict | None = None,
    ) -> FpsSample:
        label = self._label(pid, name)
        now = self._clock()
        detail: dict[str, Any] = {
            "unit_full": UNIT_FULL,
            "source": SOURCE_TEXT,
            "target": label,
            "target_name": name,
            "target_pid": pid,
            "backend": BACKEND_LABEL,
            "sampled_at": now,
        }
        path = self.backend
        if path is not None:
            detail["backend_path"] = str(path)
        if reason:
            detail["error_reason"] = reason
        if extra:
            detail.update({key: value for key, value in extra.items() if value is not None})
        return FpsSample(
            pid=pid,
            fps=fps,
            displayed_fps=displayed,
            state=state,
            error_reason=reason,
            target=label,
            backend=BACKEND_LABEL,
            detail=detail,
            sampled_at=now,
        )

    def _initial_sample(self) -> FpsSample:
        """未开始时的快照：能力不足优先（那是"这台机器没有这个能力"，不是"没开"）。"""
        blocked = self._blocked()
        pid, name = self._target()
        if blocked[0]:
            return self._make_sample(state=blocked[0], reason=blocked[1], pid=pid, name=name)
        return self._make_sample(
            state=STATE_UNKNOWN, reason="尚未开始采集", pid=pid, name=name
        )

    def _waiting_sample(self) -> FpsSample:
        blocked = self._blocked()
        pid, name = self._target()
        if blocked[0]:
            return self._make_sample(state=blocked[0], reason=blocked[1], pid=pid, name=name)
        if pid is None:
            return self._make_sample(
                state=STATE_UNKNOWN, reason="没有可采集的目标", pid=None, name=name
            )
        return self._make_sample(state=STATE_SAMPLING, reason="采集中", pid=pid, name=name)

    def snapshot(self) -> FpsSample:
        """最近一次发布的读数（线程安全；不做任何 syscall）。"""
        with self._lock:
            return self._snapshot

    def poll(self) -> FpsSample:
        """同步读一次快照；采集线程存活时也只是读缓存（不并发读管道）。"""
        return self.snapshot()

    def running(self) -> bool:
        with self._lock:
            thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def wait_stopped(self, timeout: float = 2.0) -> bool:
        """有界等待 worker 退出（不在 GUI 线程等）；返回是否已停。"""
        with self._lock:
            thread = self._thread
        if thread is threading.current_thread():
            return False
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        return not self.running()

    # ------------------------------------------------------------ 目标
    def set_target(self, pid: int | None, name: str = "") -> None:
        """设置采集目标（显式传入 pid；目标解析由调用方决定）。

        换目标会清掉终态错误（用户显式换了目标 = 一次显式重试）。会话已开时按
        :data:`TARGET_STABLE_SECONDS` / :data:`MIN_SESSION_SECONDS` 去抖，避免前台
        掠过一堆窗口就反复重开会话。

        **同一个 pid 幂等**：名字被重新解析、或每秒都拿同一份目标调一次，都不算
        "换目标"——不重置重试代次、不重置去抖（否则等于每次都重新开始等一遍）。
        """
        value = self._normalize_pid(pid)
        label = str(name or "")
        with self._lock:
            changed = value != self._target_pid
            self._target_pid = value
            self._target_name = label
            if changed:
                self._target_requested_at = self._clock()
                self._retry_epoch += 1

    # ------------------------------------------------------------ 起停
    def start(self) -> bool:
        """开始采集（幂等）：已有活动 worker 时复用，返回是否正在采。

        上一轮 ``stop()`` 之后 worker 可能还在收口（子进程要等终止命令）：此时
        ``start()`` 返回 ``False`` 且**不**并开第二条线程，调用方在自己的调和节拍上
        再调一次即可（与 :class:`~petcore.platform.sampling.SamplingLoop` 同一约定）。
        能力不足（非 Windows / 缺采集程序）同样返回 ``False``，快照写 ``unsupported``。
        """
        blocked = self._blocked()
        with self._lock:
            thread = self._thread
            alive = thread is not None and thread.is_alive()
            active = alive and self._cancel is not None and not self._cancel.is_set()
            started = None
            if not alive and not blocked[0]:
                cancel = threading.Event()
                token = object()
                self._cancel = cancel
                self._token = token
                self._snapshot = self._waiting_sample_locked()
                started = threading.Thread(
                    target=self._loop,
                    args=(cancel, token),
                    name=THREAD_NAME,
                    daemon=True,
                )
                self._thread = started
        if blocked[0]:
            pid, name = self._target()
            self._set_snapshot(
                self._make_sample(state=blocked[0], reason=blocked[1], pid=pid, name=name)
            )
            return False
        if started is None:
            return active
        started.start()
        # 每进程一次的残留会话打扫：只在这里（真的起了一场采集）触发。
        self._start_sweep_once()
        return True

    def _start_sweep_once(self) -> None:
        starter = self._sweep_starter
        if starter is None:
            return
        try:
            starter()
        except Exception:  # noqa: BLE001 - 打扫失败不影响采集
            logger.debug("帧率探针：残留会话清扫启动失败", exc_info=True)

    def _waiting_sample_locked(self) -> FpsSample:
        # 调用方持有 self._lock：只用已读字段，不再加锁。
        pid = self._target_pid
        name = self._target_name
        if pid is None:
            return self._make_sample(
                state=STATE_UNKNOWN, reason="没有可采集的目标", pid=None, name=name
            )
        return self._make_sample(state=STATE_SAMPLING, reason="采集中", pid=pid, name=name)

    def stop(self) -> None:
        """取消采集（**不 join**）：旧读数立刻清掉，收口由 worker 有界完成。"""
        with self._lock:
            cancel, self._cancel = self._cancel, None
            self._token = None
            pid, name = self._target_pid, self._target_name
            self._snapshot = self._make_sample(
                state=STATE_UNKNOWN, reason="已停止采集", pid=pid, name=name
            )
        if cancel is not None:
            cancel.set()

    def close(self, *, timeout: float = EXIT_JOIN_TIMEOUT_SECONDS) -> None:
        """最终收口（退出路径）：停采集并**有界** join，幂等。"""
        self.stop()
        self.wait_stopped(timeout)

    def shutdown(self, wait_seconds: float = 1.0) -> None:
        """退出路径：取消采集并**有界**等 worker 收口（默认 1 秒）。

        与 :meth:`stop` 的区别只在"等"：隐藏/停用只取消（GUI 线程不许被子进程的
        收口吊住）；进程要退出时必须给 worker 一点时间把官方终止命令发出去，
        否则守护线程会和解释器抢着收尾。
        """
        self.stop()
        self.wait_stopped(max(0.0, float(wait_seconds)))

    def _set_snapshot(self, sample: FpsSample) -> None:
        with self._lock:
            self._snapshot = sample

    # ------------------------------------------------------------ 主循环
    def _loop(self, cancel: threading.Event, token: object) -> None:
        runtime = _Runtime(window=FpsWindow(clock=self._clock))
        try:
            while not cancel.is_set():
                now = self._clock()
                self._supervise(runtime, now)
                self._pump(runtime)
                self._check_backend(runtime, now)
                self._publish(runtime, now, token)
                if cancel.wait(self._poll_seconds):
                    break
        except Exception:  # noqa: BLE001 - 守护线程里不许把异常抛给解释器
            logger.exception("帧率探针：主循环异常退出")
        finally:
            self._close_session(runtime)
            with self._lock:
                if self._token is token:
                    self._token = None
                    self._thread = None

    # ------------------------------------------------------------ 目标监督
    def _supervise(self, runtime: _Runtime, now: float) -> None:
        """决定这一拍该采谁：开/换/停会话。"""
        with self._lock:
            retry_epoch = self._retry_epoch
        if retry_epoch != runtime.retry_epoch:
            # 调用方显式换了目标 = 一次显式重试：终态错误与退避一起解除。
            runtime.retry_epoch = retry_epoch
            runtime.terminal = ""
            runtime.error = ""
            runtime.notice = ""
            runtime.failures = 0
            runtime.next_attempt_at = 0.0
            runtime.target_gone = False
            runtime.gone_pid = None
        runtime.blocked = self._blocked()
        if runtime.blocked[0]:
            if runtime.session is not None:
                self._close_session(runtime)
            runtime.notice = runtime.blocked[1]
            return
        pid, name = self._target()
        if pid is None:
            if runtime.session is not None:
                self._close_session(runtime)
            runtime.notice = "没有可采集的目标"
            return
        if runtime.session is not None:
            if runtime.pid == pid:
                return
            with self._lock:
                requested_at = self._target_requested_at
            # 两道闸：这场会话至少活满 MIN_SESSION_SECONDS，且新目标已稳定
            # TARGET_CHANGE_DEBOUNCE。焦点在游戏/桌宠之间每秒抖一次时，这里保证
            # 最多每 ~3 秒换一次会话，而不是每秒开关一场 ETW 采集。
            ready_at = runtime.session_started_at + self._min_session_seconds
            if requested_at is not None:
                ready_at = max(
                    ready_at,
                    requested_at + max(self._target_stable_seconds, self._target_change_debounce),
                )
            if now < ready_at:
                return
            self._close_session(runtime)
            return
        if runtime.gone_pid is not None and runtime.gone_pid == pid:
            # 目标已正常退出：同一个死 pid 不再重开会话（只有 set_target 换目标才解除）。
            return
        if runtime.terminal:
            return
        if now < runtime.next_attempt_at:
            return
        self._open_session(runtime, pid, name, now)

    def _open_session(
        self, runtime: _Runtime, pid: int, name: str, now: float
    ) -> None:
        self._close_session(runtime)
        # 换代即清窗：上一段的帧区间绝不许跨目标残留。
        runtime.window.reset()
        runtime.clock_column = ""
        runtime.clock_offset = None
        runtime.problem = ""
        runtime.raw_stderr = ""
        runtime.exit_code = None
        runtime.notice = ""
        runtime.error = ""
        runtime.target_gone = False
        runtime.gone_pid = None
        backend = self.backend
        session_name = new_session_name()
        try:
            session = self._session_factory(pid, backend, session_name)
            session.start()
        except Exception as exc:  # noqa: BLE001 - 起不来只降级成错误态
            reason = str(exc) or type(exc).__name__
            self._note_failure(runtime, None, reason)
            runtime.notice = reason
            runtime.error = reason
            logger.info("帧率探针：会话启动失败：%s", reason)
            return
        runtime.session = session
        runtime.pid = int(pid)
        runtime.name = str(name or "")
        runtime.session_name = session_name
        runtime.session_started_at = now
        runtime.failures = 0
        logger.info(
            "帧率探针：开始会话（pid=%s name=%s session=%s）", pid, name or "?", session_name
        )

    def _close_session(self, runtime: _Runtime) -> None:
        session, runtime.session = runtime.session, None
        runtime.pid = None
        runtime.name = ""
        if session is None:
            return
        try:
            session.stop()
        except Exception:  # noqa: BLE001 - 替身/异常进程
            logger.debug("帧率探针：会话停止异常", exc_info=True)
        try:
            session.close()
        except Exception:  # noqa: BLE001
            logger.debug("帧率探针：会话收口异常", exc_info=True)

    def _note_failure(self, runtime: _Runtime, code: Any, reason: str) -> None:
        """记一次失败：权限类直接转终态；否则有界退避重试。"""
        runtime.failures += 1
        runtime.error = reason
        if is_terminal_failure(code, reason) or runtime.failures >= self._max_failures:
            if not runtime.terminal:
                runtime.terminal = reason
                logger.warning("帧率探针：停止重试（%s）", reason)
            return
        backoff = self._retry_backoff_seconds * (2 ** max(0, runtime.failures - 1))
        runtime.next_attempt_at = self._clock() + backoff

    # ------------------------------------------------------------ 数据泵
    def _pump(self, runtime: _Runtime) -> None:
        """把本拍到达的行喂进窗口（表头按列名认，行按目标 pid 过滤）。"""
        session = runtime.session
        if session is None:
            return
        try:
            lines = session.drain_lines()
        except Exception:  # noqa: BLE001
            logger.debug("帧率探针：取行异常", exc_info=True)
            return
        target_pid = runtime.pid
        for line in lines:
            event = runtime.window.feed_line(line)
            # 表头缺列必须在这里被记下：此时 feed_line 返回 None，不能顺势 continue。
            if runtime.window.problem:
                runtime.problem = runtime.window.problem
                continue
            if event is None:
                continue
            runtime.clock_column = runtime.window.clock_column
            if target_pid is not None and event.pid != target_pid:
                # 不能混 pid：出现别人的行只能是数据源出了问题（PID 复用/多实例），
                # 宁可丢掉也不记到目标头上。
                continue
            now = self._clock()
            if (
                runtime.clock_offset is None
                and runtime.clock_column
                and event.time_ms is not None
            ):
                # 事件时钟 → 挂钟的一次性校准（两边推进速率相同）：之后用它识别积压。
                runtime.clock_offset = now - event.time_ms / 1000.0
            runtime.window.add_event(event, arrival=now)

    def _check_backend(self, runtime: _Runtime, now: float) -> None:
        """子进程收尾处置（退出码 / stderr 尾巴）。"""
        session = runtime.session
        if session is None:
            return
        try:
            code = session.poll()
        except Exception:  # noqa: BLE001
            code = None
        if code is None:
            return
        try:
            stderr = session.stderr_tail()
        except Exception:  # noqa: BLE001
            stderr = ""
        state, reason = classify_failure(code, stderr)
        try:
            runtime.exit_code = int(code)
        except (TypeError, ValueError):
            runtime.exit_code = None
        # 会话目标在 `_close_session` 里会被清空：先记下它，退出结论按这个 pid 记。
        target_pid = runtime.pid
        self._close_session(runtime)
        if state == STATE_OK:
            runtime.failures = 0
            if _normal_exit(code):
                # ``--terminate_on_proc_exit`` 下目标退出 = 退出码 0：这是「目标没了」，
                # 不是「临时失败」。记住这个死 pid，别再每拍对它重开会话。
                runtime.target_gone = True
                runtime.gone_pid = target_pid
                runtime.notice = "目标进程已退出采集"
            else:
                runtime.notice = "采集会话已结束"
            runtime.next_attempt_at = now + self._retry_backoff_seconds
            return
        raw = " ".join(str(stderr or "").split())[:MAX_STDERR_TEXT_CHARS]
        runtime.raw_stderr = raw
        runtime.notice = reason
        if raw:
            logger.info("帧率探针：后端 stderr 原文：%s", raw)
        self._note_failure(runtime, code, reason)

    # ------------------------------------------------------------ 发布
    def _publish(self, runtime: _Runtime, now: float, token: object) -> None:
        """按发布节拍（约 4Hz）出一份快照。

        先看节拍再构造样本：``_build_sample`` 要建字典/锁/路径字符串，按 10Hz 主循环
        每拍都做是白烧 CPU。状态变化因此最多延迟一个发布节拍（250ms），这正是
        :data:`PUBLISH_INTERVAL_SECONDS` 的语义。
        """
        if runtime.last_state and now - runtime.last_publish_at < self._publish_interval:
            return
        sample = self._build_sample(runtime, now)
        runtime.last_state = sample.state
        runtime.last_publish_at = now
        with self._lock:
            if self._token is not token:
                return
            self._snapshot = sample
        if callable(self._on_sample):
            try:
                self._on_sample(sample)
            except Exception:  # noqa: BLE001 - 回调是注入面，异常不外溢
                logger.debug("帧率探针：读数回调异常", exc_info=True)

    def _build_sample(self, runtime: _Runtime, now: float) -> FpsSample:
        """把当前运行时状态翻成一份读数（状态判定集中在这里）。"""
        pid, name = self._target()
        if runtime.session is not None:
            # 换目标去抖期间旧会话还活着：读数归属按**那一场会话**的目标写，
            # 不能拿新目标的名字去标旧 pid。
            target_pid = runtime.pid
            name = runtime.name or name
        else:
            target_pid = pid
        if runtime.blocked[0]:
            return self._make_sample(
                state=runtime.blocked[0], reason=runtime.blocked[1], pid=target_pid, name=name
            )
        if runtime.terminal:
            return self._make_sample(
                state=STATE_ERROR,
                reason=runtime.terminal,
                pid=target_pid,
                name=name,
                extra={
                    "backend_stderr": runtime.raw_stderr or None,
                    "exit_code": runtime.exit_code,
                },
            )
        if runtime.problem:
            return self._make_sample(
                state=STATE_ERROR, reason=runtime.problem, pid=target_pid, name=name
            )
        if runtime.error and runtime.session is None:
            return self._make_sample(
                state=STATE_ERROR,
                reason=runtime.error,
                pid=target_pid,
                name=name,
                extra={
                    "backend_stderr": runtime.raw_stderr or None,
                    "exit_code": runtime.exit_code,
                },
            )
        if runtime.session is None:
            reason = runtime.notice or (
                "没有可采集的目标" if pid is None else "采集会话尚未就绪"
            )
            return self._make_sample(
                state=STATE_UNKNOWN,
                reason=reason,
                pid=target_pid,
                name=name,
                # 机器可读的结论：宿主据此决定「退避 30 秒再找前台」而不是每秒重设目标。
                extra={"target_gone": True} if runtime.target_gone else None,
            )

        reading = runtime.window.reading()
        lag = self._event_lag(runtime, now, reading)
        clock_detail = {"clock": reading.clock, "session": runtime.session_name}
        if lag is not None:
            clock_detail["lag_ms"] = int(max(0.0, lag) * 1000)
        if reading.newest_arrival_at is None:
            # 一帧都还没收到：计时起点算会话开始时刻，否则"会话起来了但一直没有帧"
            # 会永远停在"采集中"，用户看着一个不动的 -- 无从判断。
            waited = now - runtime.session_started_at
            if waited > self._stale_seconds:
                reason = (
                    f"会话已进行 {waited:.0f} 秒仍未收到 Present 帧"
                    "（目标可能没有渲染或已被最小化）"
                )
                return self._make_sample(
                    state=STATE_UNKNOWN,
                    reason=reason,
                    pid=target_pid,
                    name=name,
                    extra=clock_detail,
                )
            return self._make_sample(
                state=STATE_SAMPLING,
                reason="采集中",
                pid=target_pid,
                name=name,
                extra=clock_detail,
            )
        if lag is not None and lag > self._stale_seconds:
            if runtime.clock_column:
                reason = (
                    f"数据落后现实约 {lag:.1f} 秒（管道积压或目标已停止出帧）"
                    "——不作为此刻的实时读数"
                )
            else:
                reason = (
                    f"目标超过 {self._stale_seconds:g} 秒没有新的 Present 帧"
                    "（可能暂停、最小化或已退出）"
                )
            return self._make_sample(
                state=STATE_UNKNOWN,
                reason=reason,
                pid=target_pid,
                name=name,
                extra=clock_detail,
            )
        if reading.fps is None:
            return self._make_sample(
                state=STATE_SAMPLING,
                reason="采集中",
                pid=target_pid,
                name=name,
                extra=clock_detail,
            )
        extra = {
            **clock_detail,
            "frames": reading.frames,
            "chains": len(reading.chains),
        }
        if reading.fps > MAX_FPS:
            # 段上的字段盒只有 4 位（显示成 "9999+"），真实值在这里留档。
            extra["fps_exact"] = round(reading.fps, 1)
        return self._make_sample(
            state=STATE_OK,
            fps=reading.fps,
            displayed=reading.displayed_fps,
            pid=target_pid,
            name=name,
            extra=extra,
        )

    def _event_lag(self, runtime: _Runtime, now: float, reading: FpsReading) -> float | None:
        """窗口里最新的一帧**距现在多久**（秒）——判断这个读数是不是此刻的。

        有事件时钟时用它（能同时识别管道积压与目标停出帧）；没有时退回到达时刻，
        只能判"目标是否停出帧"，这个降级写在详情 ``clock`` 里，不静默。
        """
        if (
            runtime.clock_column
            and runtime.clock_offset is not None
            and reading.newest_event_ms is not None
        ):
            return (now - runtime.clock_offset) - reading.newest_event_ms / 1000.0
        if reading.newest_arrival_at is None:
            return None
        return now - reading.newest_arrival_at


# ---------------------------------------------------------------- 进程级单例宿主
_SHARED: FpsProbe | None = None
_SHARED_LOCK = threading.Lock()


def shared_probe(**kwargs: Any) -> FpsProbe:
    """进程级共享的探针（惰性建；多宠共用同一个实例与同一场会话）。

    第一次调用决定构造参数（``target_pid`` / ``frozen`` / ``bundle_root`` …），之后
    忽略参数直接返回同一个对象。调用方用 :meth:`FpsProbe.set_target` 换目标、
    ``start``/``stop`` 控制起停；``start`` 幂等，不会开第二场会话。
    """
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = FpsProbe(**kwargs)
        return _SHARED


def reset_shared_probe(*, close: bool = True) -> None:
    """丢掉共享探针并**有界收口**（退出钩子 / 测试用）。"""
    global _SHARED
    with _SHARED_LOCK:
        probe, _SHARED = _SHARED, None
    if probe is not None and close:
        probe.close()
