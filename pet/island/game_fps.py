# -*- coding: utf-8 -*-
"""真实游戏帧率：**纯逻辑层**（PresentMon 输出解析 / 窗口统计 / 格式化 / 命令行）。

本模块零 Qt、零子进程、零系统调用（除 :func:`machine_architecture` 读两个
``PROCESSOR_*`` 环境变量），只吃字符串与数字：CSV 表头与行的解析、按 swapchain 分窗的
帧率统计、读数格式化、采集命令行拼装、后端失败归类。谁去开关子进程、谁去订阅
目标，是 ``pet/fps_monitor.py`` 的事——那一层可以注入替身，这一层可以逐条断言。

**口径（不可与其它帧率混算）**：主数值是**目标应用自己的 Present 帧率**，取官方
默认指标集里的 ``MsBetweenPresents``（"相邻两次 Present 调用的间隔"，毫秒）换算：
``fps = 1000 / mean(间隔)``。详情里的 ``displayed_fps`` 是**上屏帧率**
（``MsBetweenDisplayChange``：上一帧在屏幕上停留的时长），两者同源不同义，
**不相加、不平均、不互相替代**；窗口抓取帧数、定时器 tick、GPU 利用率都不是帧率，
本模块不收。列名与模式是**实测核定**的（见 :data:`REQUIRED_COLUMNS` 处的来源说明）：
``--v2_metrics`` 的表头里根本没有 ``MsBetweenPresents``，用错模式只会得到"永远
没有读数"。

为什么**按事件时钟**而不是 Python 的读入节奏：CSV 每一行自带这一帧在**录制时间轴上**
的时刻（``TimeInMs``，毫秒）与它的帧间隔；读进程多慢、管道怎么积压，都不改变这些
数字。所以 :class:`FrameWindows` 按**事件时间**滚动（约 :data:`WINDOW_SECONDS` 秒）
而不是"最近 N 帧"——30fps 下最近 240 帧是 8 秒，那不是"当前帧率"；也用挂钟判
"数据是不是落后于现实"（见 ``fps_monitor`` 的 event-lag 判定），避免把积压的旧事件
当此刻的实时读数。

**多条 swapchain 不相加**：一个应用可能有主渲染链与若干辅助链（DWM/叠加层/
工具窗口）。把它们的帧率加起来会得到"180fps"这种不存在的读数，所以每条
``(pid, swapchain)`` 各记一本账，主值取窗口内**仍然活着且最忙的那一条链**。
"仍然活着"由事件时间决定：已经不更新（事件落在窗口之外）的旧链不参与主值——
否则一条历史高帧率链会长期压过新链。

**有界**：行长、列数、链数、每条链的样本数、原因串长度全都有上限；越界的数据
宁可丢弃也不放大（内存与呈现宽度都不接受"数据源说了算"）。
"""
from __future__ import annotations

import csv
import itertools
import logging
import math
import os
import pathlib
import secrets
import sys
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Mapping

from pet.island.segments import (
    MAX_DETAIL_CHARS,
    STATE_ERROR,
    STATE_OK,
    STATE_SAMPLING,
    STATE_UNKNOWN,
    STATE_UNSUPPORTED,
    UNKNOWN_TEXT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 后端（PresentMon）
#: 随 Windows 包分发的官方独立控制台程序文件名（含版本号：升级必须同时改
#: 文件名、SHA256 与 :data:`SOURCE_TEXT`，避免"同名不同版"的静默替换）。
BACKEND_EXE_NAME = "PresentMon-2.6.0-x64.exe"
#: 后端在源码树 / 冻结包里的相对目录。冻结包按 ``sys._MEIPASS`` 为根（与
#: ``pet/agent_link.py::bundled_plugin_dir`` 同一约定）。
BACKEND_RELATIVE_DIR = ("resources", "presentmon")
#: 官方 v2.6.0 release 资产的 SHA256（**实机复核过**：2026-10-11 从
#: ``GameTechDev/PresentMon`` release 下载独立 exe，本地 ``sha256sum`` 与 GitHub
#: release API 的 ``digest`` 一致）。打包/CI 必须按它校验随包文件；运行期不校验
#: （每次启动读 980KB 只为一个 CI 该做的事情，代价与收益不成比例）。
REQUIRED_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
#: 采集来源（呈现层详情的"数据来源"行）。
SOURCE_TEXT = "PresentMon 2.6.0 · MsBetweenPresents（应用 Present 帧率）"
#: 非 Windows 的能力限制说明（呈现层照抄，不冒称三端都有 FPS）。
PLATFORM_UNSUPPORTED_REASON = (
    "游戏帧率只在 Windows 上可用：数据源是 ETW 上的显卡 Present 事件"
)
#: x64 判据词表：随包后端只有 x64 一个文件（构建期由 ``scripts/verify_presentmon.py``
#: 校验 PE 机器类型），所以"有能力"要求本机内核架构在这个集合里。
_X64_ARCHITECTURES = frozenset({"amd64", "x86_64", "x64", "em64t"})
#: ARM64 词表。**为什么单独一条理由**：Windows on ARM64 上这个 x64 exe 也许能靠
#: 模拟层跑起来，但**没有实机验证过**（读数对不对、ETW 行为是否一致都不知道），
#: 所以按"没有这个能力"降级并如实说明——比声称支持、然后给一堆无法解释的失败诚实。
_ARM64_ARCHITECTURES = frozenset({"arm64", "aarch64"})
ARM64_UNSUPPORTED_REASON = (
    "Windows ARM64 未验证：随包的 PresentMon 只有 x64 版本，暂无 ARM64 后端"
)
#: 其它架构（如 32 位 Windows 的 x86）：与随包的 x64 后端架构不匹配。
OTHER_ARCH_UNSUPPORTED_REASON = (
    "本机架构 {arch} 与随包的 x64 采集程序不匹配（只分发 x64）"
)

#: 段状态词表里的两个**语义别名**（值就是 ``island_segments`` 的常量，这里给的是
#: 使用场景：读代码的人不该靠猜"这个 unknown 是什么意思"）。
#: 没有可采集的目标（用户只是没开游戏 / 桌宠在前台）——**不是**错误。
NO_TARGET_STATE = STATE_UNKNOWN
#: 会话已经在跑、但还没攒够帧样本（"采集中"）——读数还没到可信的那一步。
WARMING_STATE = STATE_SAMPLING

# ---------------------------------------------------------------- 读数的界
#: 最坏宽度样本：4 位数字 + 明确的"以上"记号（见 :func:`format_fps`）。
#: **必须覆盖三位以上**——1440Hz 的显示器与无上限的 Present 循环都会给出
#: >999 的读数，按 3 位声明盒宽会让读数被截成 ``10…``（错的是盒宽，不是数据）。
MAX_TEXT = "9999+"
#: 呈现层盒宽的表示上限：超过它**不夹紧冒充**，而是显示 ``9999+``（"至少这么多"），
#: 真实值仍原样留在 ``FpsSample.fps`` 与详情里。数据侧不做上限裁决（见下文）。
MAX_FPS = 9999.0
#: 卡片与段契约共用的说明口径。措辞刻意只说**应用侧**：
#: 主数值是应用 Submit/Present 的帧率，**不保证已经上屏**（上屏帧率是详情里的另一
#: 个数，见 :data:`DISPLAYED_COLUMN`），也不是游戏自带计数器或 GPU 占用。
UNIT_FULL = "游戏应用的 Present 帧率（相邻两次 Present 的间隔）"

#: 帧区间（毫秒）的**唯一**有效性判据：有限且 > 0。刻意**不设上限**——
#: "超过 10000fps 就是坏输入"是我们没有证据的武断（合成负载、垂直同步关闭的
#: 空转循环都能超过它），数据侧只拒绝算不出速率的值（0 / 负数 / NaN / inf），
#: 显示侧的"太宽"由 :func:`format_fps` 用 ``9999+`` 表达。
#: 事件时间窗（约 1 秒）本身就是坏样本的收口：任何离谱的单个值最多影响 1 秒。
WINDOW_SECONDS = 1.0
#: 报出读数需要的**窗口覆盖**：至少两个样本，且它们的事件时间跨度 ≥ 这个值。
#: 为什么要跨度而不是固定样本数：定死"≥8 帧"会让 8fps 以下的应用永远报不出读数
#: （1 秒窗里根本攒不到 8 帧），而 5fps 是**真实且可测**的低帧率。
MIN_FRAMES = 2
MIN_SPAN_SECONDS = 0.1
#: 每条链在窗口里最多保留的样本数：1 秒窗在极端情况下（上万 fps）会攒下巨量
#: 样本，这里封顶（超出丢最老的），内存与统计成本都有界。
MAX_CHAIN_SAMPLES = 2048
#: 同时跟踪的 ``(pid, swapchain)`` 条数上限（超出按最久未更新淘汰）。
MAX_CHAINS = 8

#: 单行 CSV 的字符上限（真机一行约 300–400 字符；这里留 10 倍余量）。
#: 超长行（数据源损坏 / 被塞了别的东西）整行丢弃，**不**截断后当数据解析。
MAX_LINE_CHARS = 4096
#: 单行字段数上限：csv 语义下引号内可以有任意多个逗号，硬切之前先卡总数。
MAX_COLUMNS = 128
#: 应用名与 swapchain 地址的长度上限。应用名超长只截断（它只是标签），
#: swapchain 地址超长**整行丢弃**——地址是区分链的键，截断会让两条链撞成一个。
MAX_APPLICATION_CHARS = 64
MAX_SWAPCHAIN_CHARS = 32
#: 失败原因串上限（与段契约的详情行同一个口径）。
MAX_REASON_CHARS = MAX_DETAIL_CHARS

# ---------------------------------------------------------------- 列（官方契约）
#: **实测核定的列名**（来源：``GameTechDev/PresentMon`` @ ``v2.6.0`` 的
#: ``PresentMon/CsvOutput.cpp`` 表头分支 + ``PresentMon/CommandLine.cpp`` 的默认值，
#: 并用官方 gold 产物 ``Tests/Gold/test_case_0*.csv`` 逐字对齐）。
#:
#: 我们**不传** ``--v1_metrics`` / ``--v2_metrics``，即官方 README 的 "Default
#: metrics"：``MsBetweenPresents`` 只存在于这套（与 v1）里——``--v2_metrics`` 的表头
#: 是 ``CPUStartTime, FrameTime, CPUBusy, ...``，**没有** MsBetweenPresents
#: （v2 的帧间隔叫 ``FrameTime``，语义是"帧起始到下一帧起始"）。既然要的口径是
#: "应用 Present 的间隔"，就必须用默认这套；拿 v2 的表头解析 MsBetweenPresents
#: 只会得到"缺列 + 永远没有读数"。
#:
#: 缺任何一列都无法给出可信读数：Application/ProcessID/SwapChainAddress 是
#: "这一帧属于谁、哪条链"的身份，MsBetweenPresents 是帧率本身。
REQUIRED_COLUMNS = ("Application", "ProcessID", "SwapChainAddress", "MsBetweenPresents")
#: 主数值列：相邻两次 Present 的间隔（官方 README："The time between this Present()
#: call and the previous one, in milliseconds"）。
PRESENT_INTERVAL_COLUMN = "MsBetweenPresents"
#: 上屏时长列：**上一帧在屏幕上停留的时长**（官方 README："How long the previous
#: frame was displayed before this Present() was displayed"；``CsvOutput.cpp`` 在值
#: 为 0 时写 ``NA``——即"这一帧没有上屏"）。它与主值同源不同义，只作详情。
#: ``--no_track_display`` 会让这一列消失 → 详情留空，主值不受影响。
DISPLAYED_COLUMN = "MsBetweenDisplayChange"
#: 事件时钟列的候选序（**按优先级**）：列值是"距本次录制开始的毫秒数"
#: （``PresentMonTraceSession.cpp::TimestampToMilliSeconds``：
#: ``(timestamp - mStartTimestamp) * 1000 / frequency``），是**流内单调的真实时间**，
#: 用来按事件时间开窗与判"数据是否落后"。``TimeInMs`` 是 Present 事件自身的时刻，
#: ``CPUStartTimeInMs`` 是 CPU 开始处理这一帧的时刻；两者都随默认时间单位
#: （``CommandLine.cpp`` 默认 ``TimeUnit::MilliSeconds``）出现。
#: 都取不到时退回**到达时钟**（有记录，见 fps_monitor 的 ``detail["clock"]``）。
EVENT_CLOCK_COLUMNS = ("TimeInMs", "CPUStartTimeInMs")
#: 表头的形态判据：有这两列就认为"这是一行 CSV 表头"（据此报"缺列"而不是
#: 当噪声丢掉——数据源换了列名时必须能诊断，不能表现为"采集没数据"）。
_HEADER_SHAPE = ("Application", "ProcessID")

# ---------------------------------------------------------------- 会话名与退出码
SESSION_NAME_PREFIX = "dsh-pet-fps-"
#: 会话名长度上限（ETW 侧还有更大的余量，这里卡死是为了日志/证据里不出现长串）。
MAX_SESSION_NAME_CHARS = 64
#: PresentMon 退出码：6 = 采集会话启动失败（权限不足最常见），7 = 终止命令
#: 没找到同名会话（对我们意味着"本来就已经停了"，**不是**错误）。
EXIT_START_FAILED = 6
EXIT_NO_SESSIONS = 7
#: 会话名的进程内序号：``secrets`` 相同（理论上）或同毫秒连开也不会重名。
_session_seq = itertools.count(1)


# ---------------------------------------------------------------- 平台与后端路径
def machine_architecture() -> str:
    """本机**内核**架构的小写名（取不到返回 ``""``）。纯函数、零子进程。

    只读 Windows 自己的两个环境变量：``PROCESSOR_ARCHITECTURE`` 是本进程的架构，
    ``PROCESSOR_ARCHITEW6432`` 是 32 位/WOW64 与 ARM64 上的 x64 模拟层暴露出的
    **内核**架构（有它时优先它）。所以在 ARM64 机器上以 x64 模拟方式跑起来时，这里
    如实返回 ``arm64``，而不是报告本进程的 ``amd64``。

    **不用 ``platform.machine()``**：那个函数在 Windows 上先做一次 WMI 查询
    （``platform._get_machine_win32`` 派生 ``wmic``/PowerShell 子进程、失败才回落环境
    变量），既违反本模块"零子进程"的契约，又比读两个环境变量贵几个数量级——而它每次
    ``snapshot()``/空闲轮询都会被问到。
    """
    if sys.platform != "win32":
        return ""
    native = (os.environ.get("PROCESSOR_ARCHITEW6432")
              or os.environ.get("PROCESSOR_ARCHITECTURE") or "")
    return native.strip().lower()


def platform_problem() -> str:
    """平台/架构闸门不可用的人话原因；可用返回 ``""``（纯函数）。

    两级判据：① 平台必须是 Windows（数据源是 ETW 上的 Present 事件）；② 架构必须匹配
    随包后端（**只分发 x64**）。读不出架构时**不据此降级**——只在明确看出是 ARM64 或
    其它架构时才说"没有这个能力"，不把"环境里没有 PROCESSOR_*"当成"不支持"。
    """
    if sys.platform != "win32":
        return PLATFORM_UNSUPPORTED_REASON
    arch = machine_architecture()
    if not arch or arch in _X64_ARCHITECTURES:
        return ""
    if arch in _ARM64_ARCHITECTURES:
        return ARM64_UNSUPPORTED_REASON
    return OTHER_ARCH_UNSUPPORTED_REASON.format(arch=arch)


def platform_support() -> bool:
    """本机有没有这条采集能力（纯函数）= 平台与架构都没有问题。

    每次调用现算（不缓存）：测试要按平台/架构分支打桩，而缓存会让"这台机器没有这个
    能力"的判定在导入期就被定死。
    """
    return platform_problem() == ""


def bundled_backend_path() -> pathlib.Path:
    """内置采集程序的预期路径（**纯函数**，不碰文件系统）。

    冻结包（PyInstaller）取 ``sys._MEIPASS/resources/presentmon/...``，源码运行取
    仓库根的同一相对路径。**绝不做 PATH 查找**：同名程序在 PATH 上有别的版本
    （或用户自己装了一个）时，我们采到的就不是校验过的那一个二进制。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    root = pathlib.Path(meipass) if meipass else pathlib.Path(__file__).resolve().parents[2]
    return root.joinpath(*BACKEND_RELATIVE_DIR, BACKEND_EXE_NAME)


def backend_problem(path: Any = None) -> str:
    """内置后端不可用的人话原因；可用返回 ``""``。

    ``path`` 省略时解析 :func:`bundled_backend_path`。目录/不存在都算不可用
    （目录冒充可执行文件同样起不来，含糊放过只会把失败推到子进程那一层）。
    """
    candidate = pathlib.Path(path) if path is not None else bundled_backend_path()
    if candidate.is_file():
        return ""
    return f"缺少内置采集程序 {BACKEND_EXE_NAME}（预期路径：{candidate}）"


def backend_state(path: Any = None) -> tuple[str, str]:
    """后端可用性 → ``(段状态, 原因)``；可用时返回 ``("", "")``。

    平台/架构或二进制缺一，都不是"暂时没有数据"，而是**这台机器上没有这个能力**：
    段按 ``unsupported`` 不占位、卡片写明原因（见 ``island_segments`` 的呈现契约）。
    原因取**具体**那一条（非 Windows / ARM64 未验证 / 架构不匹配 / 缺文件），不笼统说
    "不支持"——用户看得见的那一行必须能解释到底缺什么。
    """
    problem = platform_problem() or backend_problem(path)
    if problem:
        return STATE_UNSUPPORTED, problem
    return "", ""


def parse_session_names(output: str) -> list[str]:
    """从 ``logman query -ets`` 的输出里取会话名（每行第一列）。

    表头/本地化说明行会被前缀过滤挡掉，所以这里只做"取首列"，不猜列名。
    """
    names: list[str] = []
    for line in str(output or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith(SESSION_NAME_PREFIX):
            names.append(parts[0])
    return names


def stale_session_names(output: str, keep: str = "") -> list[str]:
    """本应用前缀、但**不是本次会话**的残留 ETW 会话名。

    残留来自异常退出（进程被强杀/崩溃时走不到 ``--terminate_existing_session``）。
    Windows 对同时存在的 trace 会话有上限，积累十几个之后新会话启动直接报
    ``error 1450``（系统资源不足）——实机就是这样：整段 FPS 不出数，日志里只有
    一行 1450，而用户以前单屏玩游戏是能出数的（权限没变，是会话被自己攒满了）。
    """
    own = str(keep or "").strip()
    return [name for name in parse_session_names(output) if name != own]


def stop_session_args(name: str) -> list[str]:
    """``logman stop <名字> -ets``：停掉**自己前缀**的残留会话。

    只按名精确停自己那一场，绝不用通配/固定名去抢别人的采集（同
    :func:`new_session_name` 的纪律）。实测不需要管理员权限即可停本用户的会话。
    """
    return ["logman", "stop", str(name), "-ets"]


def new_session_name() -> str:
    """每次采集唯一的 ETW 会话名（``dsh-pet-fps-<pid>-<seq>-<rand>``）。

    **必须唯一**：固定名 + ``--stop_existing_session`` 会去停别人的采集会话
    （另一个 PresentMon、别的监控工具），那是抢别人的资源。唯一名让我们只可能
    启动/终止自己那一场会话，代价是重启后旧会话名无从枚举——所以每一条停止路径
    （正常停止 / 换目标 / 退出）都走官方 ``--terminate_existing_session``。
    """
    return "{}{}-{}-{}".format(
        SESSION_NAME_PREFIX, os.getpid(), next(_session_seq), secrets.token_hex(4))


# ---------------------------------------------------------------- 命令行（纯函数）
def build_capture_args(*, session_name: str, pid: int | None = None,
                       process_name: str = "") -> list[str]:
    """采集命令行。**只接受 PID 或进程名之一**（没有目标的采集没有意义）。

    刻意不出现的参数（每一条都是纪律，不是遗漏）：

    - ``--stop_existing_session`` / 固定会话名：会抢占别人的采集会话；
    - ``--restart_as_admin``：静默提权是用户没答应过的事；
    - ``--v1_metrics`` / ``--v2_metrics``：**口径取决于是哪一套列**。
      ``--v2_metrics`` 的表头是 ``CPUStartTime, FrameTime, CPUBusy, ...``，
      **没有** ``MsBetweenPresents``（v2 的帧间隔叫 ``FrameTime``）；我们要的
      "相邻两次 Present 的间隔"只在默认（与 v1）那套里。传了 v2 就等于让自己
      永远解析不到读数——这是实测核定的（见 :data:`REQUIRED_COLUMNS`）。
    - ``--output_file`` / ``--multi_csv`` / ``--hotkey`` / ``--delay`` / ``--timed``：
      文件输出、交互触发、定时收尾都不属于"常驻跟随前台目标"这一件事，
      收口由我们自己按需下命令（见 :func:`build_terminate_args`）。
    - ``--qpc_time`` / ``--qpc_time_ms`` / ``--date_time``：默认时间单位是毫秒
      （``CommandLine.cpp`` 的 ``TimeUnit::MilliSeconds``），事件时钟列因此叫
      ``TimeInMs``；改成 QPC 会变成裸计数器、改成日期要解析本地时间，
      而我们只需要"流内单调的毫秒"。
    - ``--write_display_metadata`` / ``--exclude_dropped``：多两列身份列、
      或改掉"丢弃帧也出现在 CSV 里"的语义，都不是我们要的。

    带上 ``--no_track_input`` / ``--no_track_gpu``：输入与 GPU 工作量的跟踪要额外
    ETW 事件，我们的读数（``MsBetweenPresents`` / ``MsBetweenDisplayChange``）都不
    依赖它们，少挂两路事件就少一份全机开销（``--no_track_display`` **不能**加：
    那会把上屏时长列一起删掉）。``--terminate_on_proc_exit`` 让目标退出时它自己
    收尾，避免我们靠轮询去发现"目标已经没了"。
    """
    name = str(session_name or "").strip()
    if not name:
        raise ValueError("采集会话名不能为空")
    if len(name) > MAX_SESSION_NAME_CHARS:
        raise ValueError(f"采集会话名过长（上限 {MAX_SESSION_NAME_CHARS}）：{name!r}")
    args = [
        "--session_name", name,
        "--output_stdout",
        "--no_console_stats",
        "--no_track_input",
        "--no_track_gpu",
        "--terminate_on_proc_exit",
    ]
    if pid is not None:
        args += ["--process_id", str(int(pid))]
    elif str(process_name or "").strip():
        args += ["--process_name", str(process_name).strip()]
    else:
        raise ValueError("采集命令行必须给 --process_id 或 --process_name")
    return args


def build_terminate_args(session_name: str) -> list[str]:
    """停止**我们自己那一场**会话的命令行（官方退出方式）。

    为什么不用 CTRL_BREAK / CTRL_C：采集子进程以 ``CREATE_NO_WINDOW`` 拉起
    （没有可见控制台），信号能不能送达取决于进程组与控制台的绑定关系，赌不起；
    ``terminate()`` 是硬杀，会话可能留在 ETW 里成为孤儿。官方的
    ``--terminate_existing_session`` 指名道姓停掉那一个会话，随后采集进程自行退出。
    """
    name = str(session_name or "").strip()
    if not name:
        raise ValueError("终止会话名不能为空")
    return ["--session_name", name, "--terminate_existing_session"]


# ---------------------------------------------------------------- 失败归类（纯函数）
def _bounded_reason(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:MAX_REASON_CHARS]


def is_terminal_failure(exit_code: Any, stderr_text: str = "") -> bool:
    """这个失败要不要**停止重试**（权限类失败重试一万次也是同样的结果）。

    权限不足（ETW 会话启动被拒）在这台机器上是稳定状态，不是抖动：用户要么给
    "Performance Log Users" 组权限、要么以管理员运行，重试只会刷日志。
    """
    text = str(stderr_text or "").lower()
    if "access denied" in text or "performance log users" in text:
        return True
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return False
    return code == EXIT_START_FAILED


def classify_backend_failure(exit_code: Any, stderr_text: str = "") -> tuple[str, str]:
    """采集进程的收尾 → ``(段状态, 人话原因)``。纯函数。

    - 权限不足：``error`` + **官方原文摘要**（"access denied" / "Performance Log
      Users" 必须留在原因里：用户拿这句话就能查到该加哪个组）；
    - ``--terminate_existing_session`` 找不到会话（退出码 7）：``ok`` —— 那是
      "本来就已经停了"，不是失败；
    - 退出码 0：``ok``（``--terminate_on_proc_exit`` 下目标退出就是这条路径）；
    - 其它非零：``error`` + 退出码 + stderr 末行（能诊断就诊断，读不到就如实说）。
    """
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = -1
    text = str(stderr_text or "")
    low = text.lower()
    if "access denied" in low or "performance log users" in low:
        # 卡片上只有一行（段契约 MAX_DETAIL_CHARS=120），所以这里给的是**可行动**
        # 的短句：两个关键词都在（用户拿它们能搜到该加哪个组 / 该不该提权），
        # 官方原文由 fps_monitor 收进 detail["backend_stderr"] 与日志，不丢证据。
        return STATE_ERROR, _bounded_reason(
            'PresentMon 权限不足：access denied —— 需要管理员权限或 '
            '"Performance Log Users" 组成员')
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
        return STATE_ERROR, _bounded_reason(f"采集进程异常退出（退出码 {code}）：{tail}")
    return STATE_ERROR, _bounded_reason(f"采集进程异常退出（退出码 {code}），且没有输出诊断信息")


# ---------------------------------------------------------------- CSV 解析（纯函数）
def split_csv_line(line: Any) -> list[str]:
    """一行 CSV → 字段列表；空行 / 超长行 / 超宽行返回 ``[]``（调用方按噪声处理）。

    用 ``csv`` 模块而不是 ``line.split(",")``：PresentMon 的字段可能带引号
    （进程名里有逗号时），硬切会把一列掰成两列，行号与列号从此全部错位。
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

    **按列名认表头，不按行号**：真机 stdout 里表头前面可能有 ``Started recording``
    之类的通知行（还可能晚几拍才出现），按"第 0 行是表头"解析会一路错位。
    """
    fields = split_csv_line(line)
    if not fields:
        return None
    names = [f.strip() for f in fields]
    if any(name not in names for name in REQUIRED_COLUMNS):
        return None
    return {name: index for index, name in enumerate(names)}


def event_clock_column(columns: Mapping[str, int] | None) -> str:
    """表头里可用的事件时钟列名（按 :data:`EVENT_CLOCK_COLUMNS` 优先级取第一个）。

    纯函数，返回 ``""`` 表示这一路输出没有可用的事件时钟，调用方退回到达时钟，
    并把这个事实写进详情（不静默降级）。
    """
    if not columns:
        return ""
    for name in EVENT_CLOCK_COLUMNS:
        if name in columns:
            return name
    return ""


def header_problem(line: Any) -> str:
    """表头形态但缺必需列 → 人话原因；不是表头（或完好）返回 ``""``。

    存在的意义：列名变了（后端升级 / 用错了指标模式）必须能被诊断成"表头缺列"，
    而不是表现为"采集中一直没有帧"——后者的排查方向完全不同。
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
        # 只有 v2 指标集才有 FrameTime：这是"用错模式"的指纹，直接点破。
        hint = "；表头看着像 --v2_metrics（v2 的帧间隔叫 FrameTime），我们用的是默认指标集"
    return _bounded_reason(
        f"采集输出表头缺少必需列：{'、'.join(missing)}{hint}")


def _bounded_ms(value: Any) -> float | None:
    """毫秒读数 → 有限且 > 0 的浮点；``NA`` / 0 / 负数 / NaN / inf 一律 ``None``。

    ``NA`` 是官方语义（该帧没有上屏、或该帧没有前驱等），必须与 0 分开：0 毫秒的
    帧间隔会换算成无穷大帧率，把它当数据就是把"没有数据"渲染成了"帧率爆表"。

    **不设上限**：>10000fps 的帧间隔（<0.1ms）不是"坏输入"，合成负载与关闭垂直同步
    的空转循环都会给出这种值；数据侧只拒绝**算不出速率**的值。
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

    与帧间隔不同，**0 是合法值**（``TimestampToMilliSeconds`` 以录制起点为零点，
    第一帧就在 0 附近），负值也保留（时间戳偶尔落在零点之前）。它只是一个坐标，
    不参与速率计算，所以不套用 :func:`_bounded_ms` 的 ">0" 判据。
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


# ---------------------------------------------------------------- 行与统计
@dataclass(frozen=True)
class FrameRow:
    """一帧的可用字段（PresentMon 一行 CSV 的**必要部分**）。

    ``event_ms`` 是这一帧在**录制时间轴**上的位置（``TimeInMs``，毫秒），
    窗口按它滚动；取不到时为 ``None``（调用方退回到达时钟）。
    """

    pid: int
    application: str
    swapchain: str
    between_ms: float | None
    displayed_ms: float | None
    event_ms: float | None = None


def parse_frame_row(line: Any, columns: Mapping[str, int] | None,
                    clock_column: str = "") -> FrameRow | None:
    """一行数据 → :class:`FrameRow`；不是数据行返回 ``None``（噪声/坏行）。

    坏行**不抛异常**：真机 stdout 里混着通知行、空行、被截断的行都发生过，
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
    return FrameRow(
        pid=pid,
        application=application,
        swapchain=swapchain,
        between_ms=_bounded_ms(value(PRESENT_INTERVAL_COLUMN)),
        displayed_ms=_bounded_ms(value(DISPLAYED_COLUMN)),
        event_ms=_event_ms(value(clock_column)) if clock_column else None,
    )


@dataclass(frozen=True)
class ChainReading:
    """一条 swapchain 的读数。"""

    pid: int
    swapchain: str
    fps: float | None
    displayed_fps: float | None
    frames: int
    #: 这条链最近的样本在**录制时间轴**上的位置（毫秒），以及我们收到它的时刻。
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
    #: 窗口内最新的样本：事件时刻（毫秒，无事件时钟时为 None）与到达时刻（秒）。
    newest_event_ms: float | None = None
    newest_arrival_at: float | None = None


class _Chain:
    """单条链的定长窗口：``(时刻, 帧间隔, 上屏时长)`` 三元组，按事件时间滚动。

    样本数由 ``maxlen`` 夹住（构造时注入，**不是** ``default_factory=deque``：
    裸 ``deque()`` 没有上界，一旦有人漏传窗口大小就是一个无声的内存漏洞）；
    时间跨度由 :meth:`prune` 夹住。两者都要：只卡条数会在上万 fps 时保留过长历史，
    只卡时间会在极端爆发时把内存交给数据源。
    """

    __slots__ = ("samples", "max_samples", "window_ms", "last_event_ms", "last_arrival_at")

    def __init__(self, max_samples: int, window_ms: float):
        self.samples: deque = deque(maxlen=max_samples)
        self.max_samples = int(max_samples)
        self.window_ms = float(window_ms)
        self.last_event_ms: float | None = None
        self.last_arrival_at: float | None = None

    def add(self, t_ms: float, interval: float | None, displayed: float | None,
            arrival: float) -> None:
        self.samples.append((float(t_ms), interval, displayed))
        self.last_event_ms = float(t_ms)
        self.last_arrival_at = float(arrival)

    def prune(self, newest_ms: float) -> None:
        """丢掉落在窗口之外的老样本（``newest_ms`` 是全局最新的事件时刻）。"""
        cutoff = newest_ms - self.window_ms
        samples = self.samples
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    # ------------------------------------------------------------ 统计
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


class FrameWindows:
    """按 ``(pid, swapchain)`` 分窗累积帧样本，给出主读数。

    - **按事件时间滚动**（``window_seconds``，默认 1 秒）：读数只由帧自带的时间
      决定。定死"最近 N 帧"是错的——30fps 下 240 帧是 8 秒，那已经过去了。
    - **不跨 pid、不跨链**：同一地址在不同进程里是两条账（PID 复用与多实例都
      真实存在），主值取最忙的那一条，**不相加**。
    - **主值只认窗口内还活着的链**：事件落在窗口外的旧链不参与主值（它已经不更新
      了），否则一条历史高帧率链会长期压过新链。
    - 链数超界时淘汰最久未更新的那条：常驻进程不该被"某个应用开了 20 个交换链"
      撑爆内存。
    """

    def __init__(self, window_seconds: float = WINDOW_SECONDS,
                 max_chains: int = MAX_CHAINS, max_samples: int = MAX_CHAIN_SAMPLES):
        self._window_ms = max(1.0, float(window_seconds) * 1000.0)
        self._max_chains = max(1, int(max_chains))
        self._max_samples = max(2, int(max_samples))
        self._chains: OrderedDict[tuple[int, str], _Chain] = OrderedDict()
        #: 全局最新事件时刻（毫秒）：窗口的右端。**只在事件时钟可用时**有值。
        self._newest_ms: float | None = None

    def reset(self) -> None:
        """清空（换目标 / 重新开始采集时调用：上一段窗口不许跨代残留）。"""
        self._chains.clear()
        self._newest_ms = None

    def add(self, row: FrameRow, now: float) -> bool:
        """喂一帧；返回是否记下了至少一个可用样本。

        ``now`` 是**我们收到这一帧的时刻**（秒，挂钟）。帧自带事件时刻（``event_ms``）
        时用它做窗口坐标；没有时退回到达时刻——两种时钟都在"秒/毫秒的真实时间"上
        单调，窗口逻辑完全一样，区别只在能不能判断"数据落后于现实"（那由
        ``fps_monitor`` 负责，见其 event-lag 判定）。

        帧间隔与上屏时长各自独立校验：只有间隔、没有上屏时长的帧仍然贡献主读数
        （该帧没上屏时这一列是 ``NA``）。
        """
        if row is None:
            return False
        interval = _bounded_ms(row.between_ms)
        displayed = _bounded_ms(row.displayed_ms)
        if interval is None and displayed is None:
            return False
        arrival = float(now)
        event_ms = _event_ms(row.event_ms)
        if event_ms is not None:
            t_ms = float(event_ms)
        else:
            # 无事件时钟：坐标换成"到达秒→毫秒"。调用方一旦发现没有事件时钟就
            # 整场都用到达时钟（表头只认一次），所以两种坐标不会在同一场会话里混。
            t_ms = arrival * 1000.0
        self._newest_ms = t_ms if self._newest_ms is None else max(self._newest_ms, t_ms)
        key = (int(row.pid), str(row.swapchain))
        chain = self._chains.get(key)
        if chain is None:
            chain = _Chain(self._max_samples, self._window_ms)
            self._chains[key] = chain
        else:
            self._chains.move_to_end(key)
        chain.add(t_ms, interval, displayed, arrival)
        newest = self._newest_ms if self._newest_ms is not None else t_ms
        for item in self._chains.values():
            item.prune(newest)
        # 淘汰放在最后：新链此刻已经有时间戳，不会因为"还没有 last_event_ms"
        # 被当成最久未更新的那条反手淘汰掉。
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

    def reading(self) -> FpsReading:
        """当前主读数 + 各链明细。

        主值的两个门槛：链在窗口内还活着（:meth:`_Chain.prune` 已把过期的链清空，
        所以 ``frames`` 就是窗口内的样本数）且窗口覆盖足够（样本数 ≥
        :data:`MIN_FRAMES` 且事件跨度 ≥ :data:`MIN_SPAN_SECONDS`）——否则按
        "采集中"呈现，不拿一两帧的抖动当读数。
        """
        newest = self._newest_ms
        chains: list[ChainReading] = []
        for (pid, swapchain), chain in self._chains.items():
            covered = (chain.present_count() >= MIN_FRAMES
                       and chain.span_ms() >= MIN_SPAN_SECONDS * 1000.0)
            fps = chain.fps() if covered else None
            chains.append(ChainReading(
                pid=pid,
                swapchain=swapchain,
                fps=fps,
                displayed_fps=chain.displayed_fps() if fps is not None else None,
                frames=chain.present_count(),
                last_event_ms=chain.last_event_ms,
                last_arrival_at=chain.last_arrival_at,
            ))
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
            newest_event_ms=newest,
            newest_arrival_at=max(arrivals) if arrivals else None,
        )


# ---------------------------------------------------------------- 格式化（纯函数）
def format_fps(value: Any) -> str:
    """帧率 → 段上的整数字段（``"144"``）；无读数 / 非数字 / 负数 → ``"--"``。

    取整是刻意的：小数会让盒内数字每秒抖动；>999 原样显示（1440Hz 显示器真实
    存在）。超出呈现盒宽（:data:`MAX_FPS`）时给 ``"9999+"``——**"至少 9999"是
    真话，夹紧成 ``9999`` 不是**：那是把一个更大的读数说成了具体值。
    真实数值仍在 ``FpsSample.fps`` 与详情里，需要精确值的一方拿得到。
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
