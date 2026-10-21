# -*- coding: utf-8 -*-
"""纯逻辑层单测：``pet/game_fps.py``（CSV 解析 / 窗口统计 / 格式化 / 命令行 / 失败归类）。

零 Qt、零子进程、零真实采集。**表头 fixture 全部是官方原始产物**，不是自造的：

- ``GOLD_*_HEADER``：逐字来自 ``GameTechDev/PresentMon`` @ ``v2.6.0`` 的
  ``Tests/Gold/test_case_0*.csv``（官方 gold 文件，由 ``Tools/create_gold_csvs.cmd``
  用 ``--qpc_time --track_frame_type --track_app_timing --track_gpu_video
  --track_pc_latency [--v1_metrics|--v2_metrics]`` 生成）。
- ``OUR_HEADER``：我们**实际**那套参数（默认指标集 + ``--no_track_input
  --no_track_gpu``、默认毫秒时间单位）按 ``PresentMon/CsvOutput.cpp`` 的表头分支
  与 ``PresentMon/CommandLine.cpp`` 的默认值逐列推出，并与 gold 默认表头的结构逐列
  对齐核对过（见本仓 ``.scratch/product-game-fps-20261011/HANDOFF.md`` 的证据索引）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pet.island import game_fps as gf


# ---------------------------------------------------------------- 官方表头 fixture
#: 官方 gold 默认指标集（``Tests/Gold/test_case_0.csv``，逐字）。
GOLD_DEFAULT_HEADER = (
    "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,"
    "AllowsTearing,PresentMode,FrameType,TimeInQPC,MsBetweenSimulationStart,"
    "MsBetweenPresents,MsBetweenDisplayChange,MsInPresentAPI,MsRenderPresentLatency,"
    "MsUntilDisplayed,MsPCLatency,CPUStartQPC,MsBetweenAppStart,MsCPUBusy,MsCPUWait,"
    "MsGPULatency,MsGPUTime,MsGPUBusy,MsGPUWait,MsVideoBusy,MsAnimationError,AnimationTime,"
    "MsFlipDelay,MsAllInputToPhotonLatency,MsClickToPhotonLatency,MsInstrumentedLatency"
)
#: 官方 gold ``--v2_metrics``（``Tests/Gold/test_case_0_v2.csv``，逐字）。
GOLD_V2_HEADER = (
    "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,"
    "AllowsTearing,PresentMode,FrameType,CPUStartQPC,FrameTime,CPUBusy,CPUWait,GPULatency,"
    "GPUTime,GPUBusy,GPUWait,VideoBusy,DisplayLatency,DisplayedTime,AnimationError,"
    "AnimationTime,MsFlipDelay,AllInputToPhotonLatency,ClickToPhotonLatency,"
    "InstrumentedLatency"
)
#: 官方 gold ``--v1_metrics``（``Tests/Gold/test_case_0_v1.csv``，逐字）。
GOLD_V1_HEADER = (
    "Application,ProcessID,SwapChainAddress,Runtime,SyncInterval,PresentFlags,Dropped,"
    "TimeInSeconds,msInPresentAPI,msBetweenPresents,AllowsTearing,PresentMode,"
    "msUntilRenderComplete,msUntilDisplayed,msBetweenDisplayChange,msFlipDelay,"
    "msUntilRenderStart,msGPUActive,msGPUVideoActive,msSinceInput,QPCTime"
)
#: 我们那套参数实际会拿到的表头（CsvOutput.cpp v2.6.0 默认分支 + CommandLine.cpp 默认值）。
OUR_HEADER = (
    "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,"
    "AllowsTearing,PresentMode,TimeInMs,MsBetweenSimulationStart,MsBetweenPresents,"
    "MsBetweenDisplayChange,MsInPresentAPI,MsRenderPresentLatency,MsUntilDisplayed,"
    "CPUStartTimeInMs,MsBetweenAppStart,MsCPUBusy,MsCPUWait,MsAnimationError,AnimationTime,"
    "MsFlipDelay"
)


def _row(header: str = OUR_HEADER, **fields) -> str:
    """按给定表头铺一行 CSV；未指定的列填 0，关心的列按名字覆盖。"""
    defaults = {
        "Application": "game.exe",
        "ProcessID": "4242",
        "SwapChainAddress": "0x000001AB",
        "TimeInMs": "100.0",
        "MsBetweenPresents": "16.666",
        "MsBetweenDisplayChange": "16.667",
    }
    defaults.update({k: str(v) for k, v in fields.items()})
    names = header.split(",")
    return ",".join(defaults.get(name, "0") for name in names)


# ---------------------------------------------------------------- 表头/行解析
def test_official_v2_header_has_no_ms_between_presents():
    """这是"为什么必须用默认指标集而不是 --v2_metrics"的机器可检证据。

    v2 的帧间隔叫 ``FrameTime``（语义：帧起始到下一帧起始），表头里**没有**
    ``MsBetweenPresents``；我们要求 Present 的口径，就不能传 ``--v2_metrics``。
    """
    names = GOLD_V2_HEADER.split(",")
    assert "MsBetweenPresents" not in names
    assert "FrameTime" in names
    assert gf.parse_header(GOLD_V2_HEADER) is None, "v2 表头必须被拒（缺必需列）"
    assert "MsBetweenPresents" in gf.header_problem(GOLD_V2_HEADER)


def test_official_default_and_our_header_parse():
    for header in (GOLD_DEFAULT_HEADER, OUR_HEADER):
        columns = gf.parse_header(header)
        assert columns is not None, header
        for name in gf.REQUIRED_COLUMNS:
            assert name in columns
        assert columns[gf.PRESENT_INTERVAL_COLUMN] == header.split(",").index(
            "MsBetweenPresents")


def test_gold_v1_header_is_a_different_case_convention():
    """v1 用不同的大小写约定（``msBetweenPresents``）：我们不改用它，但要知道它长这样。"""
    assert gf.parse_header(GOLD_V1_HEADER) is None
    assert "msBetweenPresents" in GOLD_V1_HEADER


def test_parse_header_tolerates_bom_crlf_and_spaces():
    assert gf.parse_header("\ufeff" + OUR_HEADER + "\r\n") is not None
    spaced = OUR_HEADER.replace("Application", " Application ").replace(
        "ProcessID", " ProcessID ")
    assert gf.parse_header(spaced) is not None


def test_parse_header_rejects_noise_lines():
    """stdout 前面可能有 ``Started recording`` 之类通知行——逐条都不是表头。"""
    for line in (
        "",
        "\n",
        "Started recording\n",
        "warning: PresentMon requires elevated privilege in order to query processes",
        _row(),
        "Application,ProcessID,SwapChainAddress",       # 缺 MsBetweenPresents
    ):
        assert gf.parse_header(line) is None, line


def test_header_problem_explains_missing_required_column():
    """像表头但缺必需列 → 给出可诊断原因（不是静默当噪声丢掉）。"""
    assert gf.header_problem(OUR_HEADER) == ""
    assert gf.header_problem("Started recording") == ""
    broken = OUR_HEADER.replace(",SwapChainAddress", "")
    problem = gf.header_problem(broken)
    assert "SwapChainAddress" in problem


def test_event_clock_column_picks_official_candidates():
    assert gf.event_clock_column(gf.parse_header(OUR_HEADER)) == "TimeInMs"
    cpu_only = ",".join(n for n in OUR_HEADER.split(",") if n != "TimeInMs")
    assert "CPUStartTimeInMs" in cpu_only.split(",")
    assert gf.event_clock_column(gf.parse_header(cpu_only)) == "CPUStartTimeInMs"
    assert gf.event_clock_column(gf.parse_header(GOLD_DEFAULT_HEADER)) == ""
    assert gf.event_clock_column(None) == ""


def test_parse_frame_row_basic():
    columns = gf.parse_header(OUR_HEADER)
    row = gf.parse_frame_row(_row(), columns, "TimeInMs")
    assert row is not None
    assert (row.pid, row.application, row.swapchain) == (4242, "game.exe", "0x000001AB")
    assert row.between_ms == pytest.approx(16.666)
    assert row.displayed_ms == pytest.approx(16.667)
    assert row.event_ms == pytest.approx(100.0)


def test_parse_frame_row_accepts_zero_event_time():
    """录制时间轴的第一帧就在 0 附近：事件时钟的 0 是合法值（帧间隔 0 不是）。"""
    columns = gf.parse_header(OUR_HEADER)
    row = gf.parse_frame_row(_row(TimeInMs="0.0", MsBetweenPresents="0"), columns,
                             "TimeInMs")
    assert row is not None and row.event_ms == 0.0 and row.between_ms is None


@pytest.mark.parametrize("bad", [
    "",                                     # 空行
    "   ",                                  # 全空白
    "Started recording",                    # 通知行
    "a,b,c",                                # 列数不足
    _row(ProcessID="na"),                   # pid 非数字
    _row(ProcessID="0"),                    # pid 非法
    _row(Application=""),                   # 应用名缺失
    _row(SwapChainAddress=""),              # swapchain 缺失（不能混链）
])
def test_parse_frame_row_rejects_noise(bad):
    columns = gf.parse_header(OUR_HEADER)
    assert gf.parse_frame_row(bad, columns, "TimeInMs") is None, bad


def test_parse_frame_row_na_and_bad_numbers_become_none():
    """NA 与非数字一律变 None（**不是** 0）；坏数只丢那一列，不吞整行。"""
    columns = gf.parse_header(OUR_HEADER)
    row = gf.parse_frame_row(
        _row(MsBetweenPresents="NA", MsBetweenDisplayChange="16.7"), columns, "TimeInMs")
    assert row is not None and row.between_ms is None and row.displayed_ms == pytest.approx(16.7)
    row = gf.parse_frame_row(
        _row(MsBetweenPresents="garbage", MsBetweenDisplayChange="NA"), columns, "TimeInMs")
    assert row is not None and row.between_ms is None and row.displayed_ms is None
    row = gf.parse_frame_row(
        _row(MsBetweenPresents="-5", MsBetweenDisplayChange="inf"), columns, "TimeInMs")
    assert row is not None and row.between_ms is None and row.displayed_ms is None


def test_parse_frame_row_keeps_high_frame_rates():
    """>10000fps 的间隔（<0.1ms）**不是坏输入**：数据侧不设上限（见模块说明）。"""
    columns = gf.parse_header(OUR_HEADER)
    row = gf.parse_frame_row(_row(MsBetweenPresents="0.01"), columns, "TimeInMs")
    assert row is not None and row.between_ms == pytest.approx(0.01)


def test_parse_frame_row_bounds_columns_chain_and_application():
    columns = gf.parse_header(OUR_HEADER)
    assert gf.parse_frame_row(_row(MsBetweenPresents="," * gf.MAX_COLUMNS), columns) is None
    assert gf.parse_frame_row(_row(SwapChainAddress="0x" + "A" * 200), columns) is None
    row = gf.parse_frame_row(_row(Application="g" * 300), columns)
    assert row is not None and len(row.application) <= gf.MAX_APPLICATION_CHARS


def test_parse_frame_row_row_shorter_than_columns_is_rejected():
    columns = gf.parse_header(OUR_HEADER)
    assert gf.parse_frame_row("game.exe,4242", columns) is None


def test_parse_frame_row_quoted_field_is_not_split_on_comma():
    columns = gf.parse_header(OUR_HEADER)
    row = gf.parse_frame_row(_row(Application='"my, game.exe"'), columns)
    assert row is not None and row.application == "my, game.exe"


def test_displayed_column_absent_still_yields_present_reading():
    """``--no_track_display`` 时上屏列消失：主值可用、详情为空。"""
    header = OUR_HEADER.replace(",MsBetweenDisplayChange", "")
    columns = gf.parse_header(header)
    assert columns is not None and "MsBetweenDisplayChange" not in columns
    row = gf.parse_frame_row(_row(header), columns, "TimeInMs")
    assert row is not None and row.displayed_ms is None and row.between_ms is not None


# ---------------------------------------------------------------- 窗口统计（事件时间）
def _feed(windows, chain, count, *, between_ms=16.666, pid=4242, start_ms=0.0,
          displayed_ms=None, arrival=None):
    """喂 ``count`` 帧，事件时间从 ``start_ms`` 起按间隔推进；返回最后一帧的时刻。"""
    event = float(start_ms)
    for index in range(count):
        event = start_ms + index * between_ms
        windows.add(
            gf.FrameRow(pid=pid, application="game.exe", swapchain=chain,
                        between_ms=between_ms,
                        displayed_ms=between_ms if displayed_ms is None else displayed_ms,
                        event_ms=event),
            (arrival if arrival is not None else event / 1000.0),
        )
    return event


def test_window_rolls_by_event_time_not_frame_count():
    """30fps 下"最近 240 帧"是 8 秒——那不是当前帧率；窗口按事件时间 ≈1 秒。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 240, between_ms=1000.0 / 30.0)     # 8 秒的事件时间
    reading = windows.reading()
    assert reading.fps == pytest.approx(30.0, rel=1e-3)
    assert 25 <= reading.frames <= 35, reading.frames        # ≈1 秒窗，不是 240


def test_window_reads_low_frame_rate():
    """5fps 也要报得出读数：门槛是"窗口覆盖"而不是固定样本数。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 6, between_ms=200.0)
    assert windows.reading().fps == pytest.approx(5.0, rel=1e-3)


def test_window_waits_for_minimum_coverage():
    """只覆盖 10ms 的窗口不报数（不拿一两帧的抖动当读数）。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 3, between_ms=5.0)
    assert windows.reading().fps is None
    assert windows.reading().newest_event_ms is not None


def test_window_sums_nothing_across_swapchains():
    """多 swapchain **不相加**：主值取最忙那条链，另一条只进详情。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 70, between_ms=16.666)
    _feed(windows, "0xB", 12, between_ms=100.0)
    reading = windows.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-2)
    by_chain = {c.swapchain: c for c in reading.chains}
    assert set(by_chain) == {"0xA", "0xB"}
    assert by_chain["0xB"].fps == pytest.approx(10.0, rel=1e-2)


def test_window_never_mixes_pids_on_same_swapchain_address():
    """同一地址出现在不同 pid（PID 复用/多实例）：按 (pid, chain) 分开记账，不混。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 70, between_ms=16.666, pid=111)
    _feed(windows, "0xA", 30, between_ms=33.333, pid=222, start_ms=1200.0)
    by_key = {(c.pid, c.swapchain): c for c in windows.reading().chains}
    assert set(by_key) == {(111, "0xA"), (222, "0xA")}


def test_old_chain_ages_out_and_does_not_keep_the_top_slot():
    """历史高帧率链不能长期压过新链：事件落在窗口外的链不参与主值。"""
    windows = gf.FrameWindows()
    _feed(windows, "0xOLD", 70, between_ms=16.666)            # 240/1000 的链，最后事件 ≈1150ms
    _feed(windows, "0xNEW", 80, between_ms=16.666, start_ms=1200.0)   # 新链从 1200ms 起
    reading = windows.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-2)
    assert reading.pid == 4242
    chains = {c.swapchain: c for c in reading.chains}
    assert chains["0xNEW"].fps is not None
    # 旧链若还在窗口内仍应给出读数（它是真实数据），但主值按"最忙的活链"取；
    # 这里两条都是 60fps，关键是旧链的样本已经被窗口剪掉。
    assert chains["0xOLD"].frames == 0 or chains["0xOLD"].last_event_ms >= (
        reading.newest_event_ms - gf.WINDOW_SECONDS * 1000.0)


def test_window_stays_bounded_in_samples_and_chains():
    windows = gf.FrameWindows()
    _feed(windows, "0xA", gf.MAX_CHAIN_SAMPLES * 2, between_ms=0.01)
    chain = windows.reading().chains[0]
    assert chain.frames <= gf.MAX_CHAIN_SAMPLES
    for index in range(gf.MAX_CHAINS + 3):
        _feed(windows, "0x%X" % (0x100 + index), 3, start_ms=10000.0 + index)
    assert len(windows.reading().chains) <= gf.MAX_CHAINS


def test_window_reads_high_frame_rates_beyond_the_box_width():
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 400, between_ms=1000.0 / 1440.0)
    assert windows.reading().fps == pytest.approx(1440.0, rel=1e-2)


def test_window_rejects_non_positive_intervals():
    """0 / 负数间隔算不出速率：整行被忽略（不是记成一个 0fps 的样本）。"""
    windows = gf.FrameWindows()
    for value in (0.0, -1.0):
        assert windows.add(gf.FrameRow(pid=1, application="g.exe", swapchain="0xA",
                                       between_ms=value, displayed_ms=None,
                                       event_ms=0.0), 0.0) is False
    reading = windows.reading()
    assert reading.fps is None and reading.chains == ()


def test_window_without_event_clock_falls_back_to_arrival():
    windows = gf.FrameWindows()
    for index in range(12):
        windows.add(gf.FrameRow(pid=1, application="g.exe", swapchain="0xA",
                                between_ms=16.666, displayed_ms=16.666, event_ms=None),
                    index * 0.016666)
    reading = windows.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-2)
    assert reading.newest_event_ms is not None and reading.newest_arrival_at is not None


def test_window_reset_clears_everything():
    windows = gf.FrameWindows()
    _feed(windows, "0xA", 70)
    windows.reset()
    reading = windows.reading()
    assert reading.fps is None and reading.chains == ()
    assert reading.newest_event_ms is None and reading.newest_arrival_at is None


def test_window_displayed_fps_uses_only_displayed_samples():
    """上屏列 NA 的帧不进上屏统计（官方语义：NA = 这一帧没有上屏）。"""
    windows = gf.FrameWindows()
    for index in range(70):
        windows.add(gf.FrameRow(pid=1, application="g.exe", swapchain="0xA",
                                between_ms=16.666,
                                displayed_ms=16.666 if index % 2 == 0 else None,
                                event_ms=float(index) * 16.666), float(index) * 0.016)
    reading = windows.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-2)
    assert reading.displayed_fps == pytest.approx(60.0, rel=1e-2)


# ---------------------------------------------------------------- 格式化与呈现契约
@pytest.mark.parametrize("value,expected", [
    (60.0, "60"),
    (1440.0, "1440"),
    (999.4, "999"),
    (0.4, "0"),
    (9999.0, "9999"),
    (9999.5, "9999+"),
    (10001.0, "9999+"),
    (1e6, "9999+"),
    (None, gf.UNKNOWN_TEXT),
    ("NA", gf.UNKNOWN_TEXT),
    (float("nan"), gf.UNKNOWN_TEXT),
    (float("inf"), gf.UNKNOWN_TEXT),
    (-1.0, gf.UNKNOWN_TEXT),
])
def test_format_fps(value, expected):
    assert gf.format_fps(value) == expected


def test_box_text_is_honest_about_the_overflow():
    """>盒宽时给"至少这么多"（``9999+``），**不**夹紧成 9999 冒充具体值。"""
    assert gf.MAX_TEXT == "9999+"
    assert gf.format_fps(gf.MAX_FPS) == "9999"
    assert gf.format_fps(gf.MAX_FPS * 3) == "9999+"
    assert len(gf.format_fps(gf.MAX_FPS * 3)) <= len(gf.MAX_TEXT)


def test_unit_full_states_application_present_semantics():
    """口径必须说明是**应用侧 Present 帧率**，且不得暗示"已经上屏"。"""
    assert "Present" in gf.UNIT_FULL
    assert "上屏" not in gf.UNIT_FULL


# ---------------------------------------------------------------- 平台与后端路径
def _arch_env(monkeypatch, architecture: str | None, native: str | None = None) -> None:
    """把本机架构钉死在环境变量上（``platform_support`` 只读这两个变量）。

    ``native`` = ``PROCESSOR_ARCHITEW6432``（WOW64 / ARM64 模拟层暴露的**内核**架构）。
    """
    if architecture is None:
        monkeypatch.delenv("PROCESSOR_ARCHITECTURE", raising=False)
    else:
        monkeypatch.setenv("PROCESSOR_ARCHITECTURE", architecture)
    if native is None:
        monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)
    else:
        monkeypatch.setenv("PROCESSOR_ARCHITEW6432", native)


def test_platform_support_follows_os_and_architecture(monkeypatch):
    """Windows **x64** 才有能力：非 Windows、Windows ARM64、32 位都不算。"""
    monkeypatch.setattr(sys, "platform", "win32")
    _arch_env(monkeypatch, "AMD64")
    assert gf.platform_support() is True
    assert gf.platform_problem() == ""

    _arch_env(monkeypatch, "ARM64")
    assert gf.platform_support() is False
    assert gf.platform_problem() == gf.ARM64_UNSUPPORTED_REASON

    _arch_env(monkeypatch, "x86")
    assert gf.platform_support() is False
    assert "x86" in gf.platform_problem()

    monkeypatch.setattr(sys, "platform", "darwin")
    assert gf.platform_support() is False
    assert gf.platform_problem() == gf.PLATFORM_UNSUPPORTED_REASON


def test_machine_architecture_reads_the_env_and_the_native_wow64_value(monkeypatch):
    """架构判据读环境变量（不派生 WMI 子进程），且内核架构优先于本进程架构。"""
    monkeypatch.setattr(sys, "platform", "win32")
    _arch_env(monkeypatch, "AMD64")
    assert gf.machine_architecture() == "amd64"

    # ARM64 上以 x64 模拟跑起来：本进程说 amd64、内核说 ARM64 → 必须报 arm64
    _arch_env(monkeypatch, "amd64", native="ARM64")
    assert gf.machine_architecture() == "arm64"
    assert gf.platform_support() is False, "模拟层下也不许声称支持（读数未验证）"

    # 非 Windows：不读这些变量（POSIX 上它们没有意义）
    monkeypatch.setattr(sys, "platform", "linux")
    _arch_env(monkeypatch, "AMD64")
    assert gf.machine_architecture() == ""


def test_unknown_architecture_does_not_disable_the_feature(monkeypatch):
    """环境里读不出架构时**不据此降级**：宁可照常尝试，也不把读不到当成不支持。"""
    monkeypatch.setattr(sys, "platform", "win32")
    _arch_env(monkeypatch, None)
    assert gf.machine_architecture() == ""
    assert gf.platform_problem() == ""
    assert gf.platform_support() is True


def test_platform_reasons_fit_the_card_line_and_say_why():
    """三条能力原因都必须能进卡片那一行（≤120），且各自说清**是哪一层**缺。"""
    reasons = (gf.PLATFORM_UNSUPPORTED_REASON, gf.ARM64_UNSUPPORTED_REASON,
               gf.OTHER_ARCH_UNSUPPORTED_REASON.format(arch="x86"))
    for reason in reasons:
        assert reason and len(reason) <= gf.MAX_REASON_CHARS, reason
        assert "\n" not in reason
    assert "Windows" in gf.PLATFORM_UNSUPPORTED_REASON
    assert "ARM64" in gf.ARM64_UNSUPPORTED_REASON and "未验证" in gf.ARM64_UNSUPPORTED_REASON
    assert "x64" in gf.OTHER_ARCH_UNSUPPORTED_REASON.format(arch="x86")


def test_backend_state_reports_the_architecture_gate_before_the_missing_file(
        tmp_path, monkeypatch):
    """架构不匹配时说架构那一条，不说"缺少文件"（后者会把人引去重装）。"""
    monkeypatch.setattr(sys, "platform", "win32")
    _arch_env(monkeypatch, "ARM64")
    state, reason = gf.backend_state(tmp_path / "nope.exe")
    assert state == gf.STATE_UNSUPPORTED
    assert reason == gf.ARM64_UNSUPPORTED_REASON


def test_bundled_backend_path_is_repo_or_meipass(monkeypatch):
    """纯函数：源码运行 → 仓库根下 resources/presentmon；冻结 → sys._MEIPASS 下。"""
    path = gf.bundled_backend_path()
    assert path.name == gf.BACKEND_EXE_NAME
    assert path.parts[-3:] == ("resources", "presentmon", gf.BACKEND_EXE_NAME)

    monkeypatch.setattr(sys, "_MEIPASS", r"C:\frozen\app", raising=False)
    frozen = gf.bundled_backend_path()
    assert frozen == Path(r"C:\frozen\app") / "resources" / "presentmon" / gf.BACKEND_EXE_NAME


def test_backend_problem_reports_missing_and_present(tmp_path):
    missing = tmp_path / "nope.exe"
    problem = gf.backend_problem(missing)
    assert problem and gf.BACKEND_EXE_NAME in problem
    real = tmp_path / gf.BACKEND_EXE_NAME
    real.write_bytes(b"MZ")
    assert gf.backend_problem(real) == ""
    # 目录冒充可执行文件同样算"没有可用后端"
    assert gf.backend_problem(tmp_path) != ""


def test_platform_support_never_lies_on_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert gf.platform_support() is False


# ---------------------------------------------------------------- 命令行（纯函数）
def test_capture_args_use_the_default_metric_set():
    args = gf.build_capture_args(session_name="dsh-pet-fps-1-ab", pid=4242)
    assert args[0:2] == ["--session_name", "dsh-pet-fps-1-ab"]
    assert args[args.index("--process_id") + 1] == "4242"
    assert "--output_stdout" in args and "--no_console_stats" in args
    assert "--terminate_on_proc_exit" in args
    assert "--no_track_input" in args and "--no_track_gpu" in args
    # 指标模式必须留空（v2 表头缺 MsBetweenPresents，见官方 fixture 的用例）
    assert "--v2_metrics" not in args and "--v1_metrics" not in args
    for forbidden in ("--stop_existing_session", "--restart_as_admin", "--output_file",
                      "--multi_csv", "--hotkey", "--etl_file", "--timed", "--delay",
                      "--no_track_display", "--qpc_time", "--qpc_time_ms", "--date_time",
                      "--write_display_metadata", "--exclude_dropped"):
        assert forbidden not in args, forbidden


def test_capture_args_process_name_mode():
    args = gf.build_capture_args(session_name="s", process_name="game.exe")
    assert args[args.index("--process_name") + 1] == "game.exe"
    assert "--process_id" not in args


def test_capture_args_require_a_target():
    with pytest.raises(ValueError):
        gf.build_capture_args(session_name="s")


def test_terminate_args_only_touch_our_own_session_name():
    args = gf.build_terminate_args("dsh-pet-fps-1-ab")
    assert args == ["--session_name", "dsh-pet-fps-1-ab", "--terminate_existing_session"]
    assert "--stop_existing_session" not in args


def test_session_name_is_unique_and_bounded(monkeypatch):
    monkeypatch.setattr(gf.secrets, "token_hex", lambda n: "a" * (n * 2))
    assert gf.new_session_name() != gf.new_session_name()
    name = gf.new_session_name()
    assert name.startswith("dsh-pet-fps-")
    assert len(name) <= gf.MAX_SESSION_NAME_CHARS
    assert name == name.strip() and " " not in name


# ---------------------------------------------------------------- 失败归类
PERMISSION_STDERR = (
    "warning: PresentMon requires elevated privilege in order to query processes that are\n"
    "         short-running or started on another account.\n"
    "error: failed to start trace session: access denied.\n"
    "       PresentMon requires either administrative privileges or to be run by a user in the\n"
    '       "Performance Log Users" user group.  View the readme for more details.\n'
)


def test_classify_permission_denied_keeps_official_wording():
    state, reason = gf.classify_backend_failure(6, PERMISSION_STDERR)
    assert state == gf.STATE_ERROR
    assert "access denied" in reason
    assert "Performance Log Users" in reason
    assert len(reason) <= gf.MAX_REASON_CHARS


def test_classify_permission_denied_is_terminal():
    """权限拒绝不是"再试一次就好了"：归类为终态，不许自动重试风暴。"""
    assert gf.is_terminal_failure(6, PERMISSION_STDERR) is True


def test_classify_unknown_session_on_terminate_is_not_an_error():
    """``--terminate_existing_session`` 找不到会话 = 本来就已经停了（退出码 7）。"""
    state, reason = gf.classify_backend_failure(
        7, "error: no existing sessions found: dsh-pet-fps-1-ab\n")
    assert state == gf.STATE_OK and reason == ""


def test_classify_exit_zero_is_normal_end():
    assert gf.classify_backend_failure(0, "")[0] == gf.STATE_OK
    assert gf.is_terminal_failure(0, "") is False


def test_classify_generic_failure_is_retryable_with_code_and_tail():
    state, reason = gf.classify_backend_failure(9, "line one\nsome other failure\n")
    assert state == gf.STATE_ERROR
    assert "9" in reason and "some other failure" in reason
    assert gf.is_terminal_failure(9, "some other failure") is False


def test_classify_failure_without_stderr_still_explains():
    state, reason = gf.classify_backend_failure(3, "")
    assert state == gf.STATE_ERROR and reason and "3" in reason


def test_classify_bounds_huge_stderr():
    state, reason = gf.classify_backend_failure(12, "\n".join(["x" * 500] * 50))
    assert state == gf.STATE_ERROR
    assert len(reason) <= gf.MAX_REASON_CHARS


def test_parse_session_names_takes_first_column():
    """``logman query -ets`` 的输出里只认本应用前缀的会话名（每行首列）。"""
    import pet.island.game_fps as gf

    output = (
        "\nData Collector Set                      Type                          Status\n"
        "-------------------------------------------------------------------------------\n"
        "NT Kernel Logger                        Trace                         Running\n"
        "dsh-pet-fps-15520-37-b4e46ca9           Trace                         Running\n"
        "Eventlog-Security                       Trace                         Running\n"
        "dsh-pet-fps-31060-20-c4294783           Trace                         Running\n"
    )
    assert gf.parse_session_names(output) == [
        "dsh-pet-fps-15520-37-b4e46ca9",
        "dsh-pet-fps-31060-20-c4294783",
    ]


def test_stale_session_names_excludes_the_current_session():
    """残留清理只针对**不是本次**的会话，且绝不碰别人前缀的采集。"""
    import pet.island.game_fps as gf

    output = (
        "dsh-pet-fps-15520-37-b4e46ca9  Trace  Running\n"
        "dsh-pet-fps-99999-1-aaaaaaaa   Trace  Running\n"
        "NVIDIA-Nsight-Etw              Trace  Running\n"
        "dsh-pet-fps-other-tool-x       Trace  Running\n"
    )
    keep = "dsh-pet-fps-99999-1-aaaaaaaa"
    assert gf.stale_session_names(output, keep=keep) == [
        "dsh-pet-fps-15520-37-b4e46ca9",
        "dsh-pet-fps-other-tool-x",
    ]
    assert gf.stop_session_args("dsh-pet-fps-a") == ["logman", "stop", "dsh-pet-fps-a", "-ets"]
