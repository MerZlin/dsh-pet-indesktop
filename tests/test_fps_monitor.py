# -*- coding: utf-8 -*-
"""采集生命周期单测：``pet/fps_monitor.py``。

**不开用户程序、不起真实进程**：子进程由假会话替身顶替（``session_factory``），
前台窗口/进程身份/进程名定位全是注入的调用面。时序断言一律事件同步 + 宽预算，
不用固定 sleep 猜时序（CI runner 比本机慢数倍）。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from pet.island import fps_monitor as fm
from pet.island import game_fps as gf
from pet.island import segments as seg


# ---------------------------------------------------------------- 替身
class FakeForeground:
    """前台窗口替身：脚本化 ``(pid, name)``，可随时改写。"""

    def __init__(self, value=None):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


class FakeIdentity:
    """进程身份（create_time）替身：按 pid 给值，可改写以模拟 PID 复用。"""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls: list[int] = []

    def __call__(self, pid):
        self.calls.append(int(pid))
        return self.values.get(int(pid))


class FakeLookup:
    """按进程名定位替身。"""

    def __init__(self, value=None):
        self.value = value
        self.calls: list[str] = []

    def __call__(self, name):
        self.calls.append(str(name))
        return self.value


class FakeSession:
    """PresentMon 会话替身：与 :class:`fm.FpsSession` 的公开契约同形。"""

    def __init__(self, pid, name, session_name):
        self.pid = int(pid)
        self.target_name = str(name)
        self.session_name = str(session_name)
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.exit_code: int | None = None
        self.stderr = ""
        #: 每次 ``stop`` 拿到的 ``terminate_session``（退出编排靠它区分"官方终止命令"
        #: 与"会话结束路径只停不派生"）。
        self.terminate_flags: list[bool] = []
        #: 事件时钟游标（毫秒）：连续调用 feed_frames 时接在上一批之后，
        #: 与真机一样单调递增。
        self.event_ms = 0.0
        self._lines: deque[str] = deque()
        self._lock = threading.Lock()

    # --- 替身侧驱动
    def feed(self, *lines: str) -> None:
        with self._lock:
            self._lines.extend(lines)

    def feed_frames(self, count: int, *, between_ms: float = 16.666,
                    displayed_ms=None, pid: int | None = None,
                    chain: str = "0x0000A1", with_header: bool = False,
                    event_step_ms: float | None = None,
                    header: str | None = None) -> float:
        """喂 ``count`` 帧；返回喂完后的**事件时钟**位置（毫秒）。

        ``event_step_ms`` 缺省 = ``between_ms``（事件时钟与帧间隔同步推进，真机常态）。
        传一个更小的步长可以模拟"事件时钟走得比现实慢"（管道积压）。
        ``header`` 用于"表头被改过"的用例：行必须与它用同一份列名，否则整行错位。
        """
        step = between_ms if event_step_ms is None else float(event_step_ms)
        target_header = header or _HEADER
        rows = [target_header] if with_header else []
        for _ in range(count):
            self.event_ms += step
            rows.append(_frame(
                pid if pid is not None else self.pid, chain, between_ms,
                between_ms if displayed_ms is None else displayed_ms,
                self.event_ms, target_header,
            ))
        self.feed(*rows)
        return self.event_ms

    def exit(self, code: int, stderr: str = "") -> None:
        self.stderr = stderr
        self.exit_code = int(code)

    # --- 会话契约（被监控方调用）
    def start(self) -> None:
        self.started += 1

    def drain_lines(self) -> list[str]:
        with self._lock:
            lines = list(self._lines)
            self._lines.clear()
        return lines

    def stderr_tail(self) -> str:
        return self.stderr

    def poll(self) -> int | None:
        return self.exit_code

    def stop(self, timeout: float = 1.0, *, terminate_session: bool = True) -> None:
        self.stopped += 1
        self.terminate_flags.append(bool(terminate_session))

    def close(self) -> None:
        self.closed += 1


class SessionLog:
    """``session_factory`` 替身：记录每次开会话的参数。"""

    def __init__(self, *, exit_code: int | None = None, stderr: str = ""):
        self.sessions: list[FakeSession] = []
        self._exit_code = exit_code
        self._stderr = stderr

    def __call__(self, pid, name, session_name) -> FakeSession:
        session = FakeSession(pid, name, session_name)
        self.sessions.append(session)
        if self._exit_code is not None:
            session.exit(self._exit_code, self._stderr)
        return session

    @property
    def latest(self) -> FakeSession:
        assert self.sessions, "还没有开过会话"
        return self.sessions[-1]


#: 我们那套参数实际拿到的官方 v2.6.0 表头（见 tests/test_game_fps.py 的来源说明）。
_HEADER = (
    "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,"
    "AllowsTearing,PresentMode,TimeInMs,MsBetweenSimulationStart,MsBetweenPresents,"
    "MsBetweenDisplayChange,MsInPresentAPI,MsRenderPresentLatency,MsUntilDisplayed,"
    "CPUStartTimeInMs,MsBetweenAppStart,MsCPUBusy,MsCPUWait,MsAnimationError,AnimationTime,"
    "MsFlipDelay"
)


def _frame(pid, chain, between_ms, displayed_ms="NA", event_ms=0.0,
           header: str = None) -> str:
    """按给定表头（默认我们那套官方表头）铺一行。

    **行与表头必须用同一个字符串**：列名→下标是从表头推出来的，改表头不改行
    就会让每一列都错位（真机里就表现为"读数莫名其妙"，所以测试也不许犯）。
    """
    values = {
        "Application": "game.exe",
        "ProcessID": str(int(pid)),
        "SwapChainAddress": chain,
        "PresentRuntime": "DXGI",
        "SyncInterval": "1",
        "PresentFlags": "0",
        "AllowsTearing": "0",
        "PresentMode": "Hardware: Legacy Flip",
        "TimeInMs": "%.4f" % float(event_ms),
        "MsBetweenSimulationStart": "NA",
        "MsBetweenPresents": "%.4f" % float(between_ms),
        "MsBetweenDisplayChange": ("NA" if displayed_ms is None else str(displayed_ms)),
        "MsInPresentAPI": "0.0893",
        "MsRenderPresentLatency": "0.0673",
        "MsUntilDisplayed": "16.4296",
        "CPUStartTimeInMs": "%.4f" % float(event_ms),
        "MsBetweenAppStart": "%.4f" % float(between_ms),
        "MsCPUBusy": "16.3000",
        "MsCPUWait": "0.0893",
        "MsAnimationError": "NA",
        "AnimationTime": "332.7030",
        "MsFlipDelay": "NA",
    }
    return ",".join(values[name] for name in (header or _HEADER).split(","))


#: 一次“应该报出读数”的喂帧量：12 帧 @60fps ≈ 183ms 事件时间，覆盖窗口的
#: MIN_SPAN_SECONDS(100ms) 门槛。窗口按事件时间滚动，帧数本身不再是门槛。
_READING_FRAMES = 12


def _dummy_backend() -> Path:
    """测试用"存在的后端"：真身不在仓库（打包那一步才进），这里只要 is_file 成立。"""
    path = Path(tempfile.mkdtemp(prefix="dsh-fps-backend-")) / gf.BACKEND_EXE_NAME
    path.write_bytes(b"MZ")
    return path


#: 默认注入的后端路径（存在但从不被真的执行——会话全是替身）。
_BACKEND = _dummy_backend()


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.005) -> bool:
    """事件同步等待（宽预算）：不猜时序，只看条件成不成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class FakeClock:
    """真实时间的可跳跃版本：平时与 ``time.monotonic`` 同步前进（时序逻辑照常），
    ``advance`` 可以一次性把时钟推到未来（用来造"现实走了 5 秒、事件没跟上"）。"""

    def __init__(self, start: float = 1000.0):
        self._lock = threading.Lock()
        self._offset = float(start) - time.monotonic()

    def __call__(self) -> float:
        with self._lock:
            return time.monotonic() + self._offset

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._offset += float(seconds)


def _monitor(**kwargs) -> fm.FpsMonitor:
    """测速用监控：时序常数压到毫秒级，其余面全是注入替身。"""
    params = dict(
        foreground_reader=FakeForeground(),
        process_identity=FakeIdentity(),
        process_lookup=FakeLookup(),
        session_factory=SessionLog(),
        backend_path=_BACKEND,
        platform_supported=lambda: True,
        target_stable_seconds=0.05,
        min_session_seconds=0.0,
        hold_seconds=0.15,
        stale_seconds=0.2,
        poll_seconds=0.01,
        publish_interval=0.01,
        max_failures=3,
        retry_backoff_seconds=0.05,
        identity_recheck_seconds=0.05,
        rescan_seconds=0.05,
    )
    params.update(kwargs)
    return fm.FpsMonitor(**params)


def _live_threads(name: str = "pet-fps-monitor") -> int:
    return len([t for t in threading.enumerate() if t.name == name and t.is_alive()])


@pytest.fixture(autouse=True)
def _no_orphan_threads():
    yield
    # 线程退出到 enumerate 可见在满载 runner 上有数秒调度延迟（macOS CI 实测超 5s）
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and _live_threads():
        time.sleep(0.01)
    assert _live_threads() == 0, "有采集线程没退出（线程泄漏）"


# ---------------------------------------------------------------- 零开销契约
def test_no_subscribers_means_no_thread_process_or_target_query():
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.start()
    time.sleep(0.15)
    assert monitor.running is False
    assert log.sessions == []
    assert fg.calls == 0, "没有订阅者时连目标都不该枚举"
    assert monitor.snapshot().fps is None
    monitor.close()


def test_unsupported_platform_never_spawns_worker():
    log = SessionLog()
    monitor = _monitor(platform_supported=lambda: False, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    time.sleep(0.1)
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_UNSUPPORTED and sample.error_reason
    assert log.sessions == [] and monitor.running is False
    monitor.close()


def test_injected_capability_is_not_overruled_by_the_real_platform_gate(monkeypatch):
    """能力判据只问注入的那一次：真实平台判据说非 Windows，注入说有能力 → 照常采。

    为什么单独锁这条：``game_fps.backend_state`` 会**再查一次真实的** ``sys.platform``，
    于是"注入能力可用"的替身用例在非 Windows 宿主上会静默不启动——同一份代码在开发机
    （Windows）跑绿、在 CI（Linux/macOS）红。平台判据只许问注入的那一次；后端仍然要查，
    但只查文件（``backend_problem`` 不碰平台，缺程序照样 ``unsupported``，见下一条用例）。
    """
    monkeypatch.setattr(gf, "platform_problem", lambda: gf.PLATFORM_UNSUPPORTED_REASON)
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    assert monitor.start() is True, "注入的能力可用时 start 必须真的起线程"
    assert _wait_until(lambda: log.sessions and log.latest.started), \
        "会话没开起来：真实平台判据把注入的能力压掉了"
    assert log.latest.pid == 4242
    sample = monitor.snapshot()
    assert sample.state != gf.STATE_UNSUPPORTED
    assert gf.PLATFORM_UNSUPPORTED_REASON not in (sample.error_reason or "")
    assert fg.calls > 0, "有能力就必须真的去解析目标（不是只起了个空线程）"
    monitor.close()


def test_default_capability_gate_still_blocks_and_keeps_the_platform_reason(monkeypatch):
    """默认能力判据（没注入，取 ``game_fps.platform_support``）说没能力 → 不建线程、不起会话。

    与上一条配对：注入只是**换掉那一次判定**，不是把它关掉——没注入时平台闸门照拦，
    原因照抄平台那一句（非 Windows / ARM64 未验证 / 架构不匹配都走同一条）。
    """
    monkeypatch.setattr(gf, "platform_support", lambda: False)
    monkeypatch.setattr(gf, "platform_problem", lambda: gf.PLATFORM_UNSUPPORTED_REASON)
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(platform_supported=None, foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    assert monitor.start() is False, "没有这个能力时不许起线程"
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_UNSUPPORTED
    assert sample.error_reason == gf.PLATFORM_UNSUPPORTED_REASON
    assert log.sessions == [] and monitor.running is False
    assert fg.calls == 0, "没有这个能力时连目标都不该枚举"
    monitor.close()


def test_missing_backend_reports_unsupported_with_expected_path(tmp_path):
    log = SessionLog()
    missing = tmp_path / gf.BACKEND_EXE_NAME
    monitor = _monitor(backend_path=missing, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    time.sleep(0.1)
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_UNSUPPORTED
    assert gf.BACKEND_EXE_NAME in sample.error_reason
    assert log.sessions == [] and monitor.running is False
    monitor.close()


def test_default_backend_path_is_resolved_at_start(tmp_path, monkeypatch):
    """不传 backend_path 时按内置路径解析（不是 PATH 盲启动），缺了就 unsupported。"""
    log = SessionLog()
    monkeypatch.setattr(gf, "bundled_backend_path", lambda: tmp_path / "absent.exe")
    monitor = _monitor(backend_path=None, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert monitor.snapshot().state == gf.STATE_UNSUPPORTED
    assert "absent.exe" in monitor.snapshot().error_reason
    assert log.sessions == []
    monitor.close()


def test_snapshot_before_start_says_not_started():
    monitor = _monitor()
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_UNKNOWN and sample.fps is None
    monitor.close()


# ---------------------------------------------------------------- 采集主链路
def test_frames_become_ok_snapshot_with_target_identity():
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    samples: list[fm.FpsSample] = []
    monitor = _monitor(foreground_reader=fg, session_factory=log, on_sample=samples.append)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions), "应当为前台目标开会话"
    session = log.latest
    assert (session.pid, session.target_name) == (4242, "game.exe")
    assert session.started == 1 and session.session_name.startswith("dsh-pet-fps-")

    session.feed_frames(70, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    sample = monitor.snapshot()
    assert sample.fps == pytest.approx(60.0, rel=2e-2)
    assert sample.displayed_fps == pytest.approx(60.0, rel=2e-2)
    assert sample.target_pid == 4242 and sample.target_name == "game.exe"
    assert sample.error_reason == "" and sample.generation >= 1
    # 时钟与落后量必须写进详情：读数是否"此刻"的，宿主与排查都看得到。
    assert sample.detail["clock"] == "TimeInMs"
    assert "lag_ms" in sample.detail and sample.detail["lag_ms"] >= 0
    assert _wait_until(lambda: samples), "on_sample 应当收到同一份快照"
    assert samples[-1].state == gf.STATE_OK
    monitor.close()


def test_frames_without_event_clock_report_arrival_clock():
    """没有事件时钟列（列被改/换模式）→ 退回到达时钟，并**在详情里说明**。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    session = log.latest
    arrival_header = _HEADER.replace(",TimeInMs", "").replace(",CPUStartTimeInMs", "")
    session.feed(arrival_header)
    # 到达时钟的窗口按"我们收到的时刻"滚动：帧真要在一段时间里陆续到达
    # （真机上就是持续读取），一次性灌进去等于零跨度、不报数。
    for _ in range(10):
        session.feed_frames(7, header=arrival_header)
        time.sleep(0.03)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    assert monitor.snapshot().detail["clock"] == "arrival"
    monitor.close()


def test_backlogged_stream_is_not_reported_as_live():
    """管道积压：事件还在来，但它们已经是几秒前的历史 → 不许当此刻的实时读数。

    用假时钟把"现实"推进 5 秒，而事件时钟只走了一点点（真机里就是读取被拖慢、
    我们手上最新的帧早已过时）。判据是**事件时钟与现实的差**。
    """
    clock = FakeClock()
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log, clock=clock,
                       stale_seconds=0.5)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    session = log.latest
    session.feed_frames(70, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)

    # 现实前进 5 秒，事件时钟只前进 1 帧的量级（= 我们读到的是 5 秒前的历史）
    clock.advance(5.0)
    session.feed_frames(3, event_step_ms=1.0)
    assert _wait_until(
        lambda: monitor.snapshot().state == gf.STATE_UNKNOWN, timeout=3.0)
    sample = monitor.snapshot()
    assert sample.fps is None, "积压的历史数据不能冒充此刻的实时读数"
    assert "落后" in sample.error_reason and "积压" in sample.error_reason
    assert sample.detail["lag_ms"] >= 3000, sample.detail
    monitor.close()


def test_long_lived_session_is_not_recycled_periodically():
    """生命周期的收口靠停止命令，不靠"到点换一场"（换会话会在换的瞬间断数据）。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    for _ in range(6):
        log.latest.feed_frames(20)
        time.sleep(0.1)
    time.sleep(0.3)
    assert len(log.sessions) == 1, "会话不该被周期性重开"
    assert log.latest.stopped == 0
    monitor.close()


def test_reading_beyond_the_box_width_keeps_the_exact_value():
    """>9999fps：段上显示 ``9999+``，详情里保留真实值（不夹紧冒充 9999）。"""
    high_fps = 12000.0
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed(_HEADER)
    log.latest.feed_frames(4000, between_ms=1000.0 / high_fps)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    sample = monitor.snapshot()
    assert sample.fps == pytest.approx(high_fps, rel=5e-2)
    assert gf.format_fps(sample.fps) == "9999+"
    assert sample.detail["fps_exact"] == pytest.approx(high_fps, rel=5e-2)
    monitor.close()


def test_low_frame_rate_still_reports_a_reading():
    """5fps 是真实可测的低帧率：窗口按事件时间 ≈1 秒，覆盖到了就该报数。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(6, between_ms=200.0, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    assert monitor.snapshot().fps == pytest.approx(5.0, rel=5e-2)
    monitor.close()


def test_stdout_noise_before_header_is_tolerated():
    """真机 stdout 前面有 ``Started recording`` 之类通知，表头可能延后出现。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    session = log.latest
    session.feed("Started recording", "", "warning: elevated privilege required")
    time.sleep(0.05)
    assert monitor.snapshot().state in (gf.STATE_SAMPLING, gf.STATE_UNKNOWN)
    session.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    monitor.close()


def test_rows_from_other_pids_are_dropped():
    """不能混 pid：会话只认自己那个目标进程的行。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed(_HEADER)
    log.latest.feed_frames(_READING_FRAMES, pid=9999)
    time.sleep(0.15)
    sample = monitor.snapshot()
    assert sample.fps is None, "不能拿别人的帧当目标读数"
    assert sample.state in (gf.STATE_SAMPLING, gf.STATE_UNKNOWN)
    monitor.close()


def test_no_frames_never_reports_zero_fps():
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed(_HEADER)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_SAMPLING)
    assert monitor.snapshot().fps is None
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_UNKNOWN, timeout=3.0)
    assert monitor.snapshot().fps is None
    monitor.close()


def test_paused_target_clears_value_instead_of_freezing_it():
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_UNKNOWN, timeout=3.0)
    sample = monitor.snapshot()
    assert sample.fps is None and sample.displayed_fps is None and sample.error_reason
    monitor.close()


def test_bad_header_reports_diagnosable_error():
    """表头认不出来（列改名/损坏）→ 不能静默当"没有帧"，要给得出原因。"""
    fg = FakeForeground((4242, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed("Application,ProcessID,SwapChainAddress\n")   # 缺 MsBetweenPresents
    assert _wait_until(
        lambda: "MsBetweenPresents" in monitor.snapshot().error_reason, timeout=3.0)
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_ERROR and sample.fps is None
    monitor.close()


# ---------------------------------------------------------------- 目标切换
def test_stable_foreground_switch_swaps_session_serially():
    fg = FakeForeground((111, "game-a.exe"))
    identity = FakeIdentity({111: 1.0, 222: 2.0})
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, process_identity=identity,
                       session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    first = log.latest
    first.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)

    fg.value = (222, "game-b.exe")
    assert _wait_until(lambda: len(log.sessions) == 2)
    second = log.latest
    assert second.pid == 222 and second is not first
    assert second.session_name != first.session_name, "会话名必须每次唯一"
    assert _wait_until(lambda: first.stopped >= 1), "换目标必须先停上一个会话"
    assert first.closed >= 1
    assert monitor.snapshot().state in (gf.STATE_SAMPLING, gf.STATE_UNKNOWN)

    # 旧会话的迟到行不得再被接受（哪怕采样率完全对不上）
    first.feed_frames(_READING_FRAMES, between_ms=100.0)
    second.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    sample = monitor.snapshot()
    assert sample.fps == pytest.approx(60.0, rel=2e-2)
    assert sample.target_pid == 222
    monitor.close()


def test_rapid_foreground_flicker_does_not_thrash_sessions():
    fg = FakeForeground((111, "game-a.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log,
                       target_stable_seconds=0.2)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    for pid in (222, 333, 444, 111):
        fg.value = (pid, f"game-{pid}.exe")
        time.sleep(0.02)          # 小于稳定窗口：都不算
    time.sleep(0.15)
    assert len(log.sessions) == 1, "抖动不该换来换去开会话"
    monitor.close()


def test_pid_reuse_is_isolated_by_process_create_time():
    fg = FakeForeground((111, "game-a.exe"))
    identity = FakeIdentity({111: 1.0})
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, process_identity=identity,
                       session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    first = log.latest
    first.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)

    identity.values[111] = 9.0          # 同 pid，但是新进程（PID 复用）
    assert _wait_until(lambda: len(log.sessions) == 2, timeout=3.0)
    assert log.latest is not first and log.latest.pid == 111
    assert _wait_until(lambda: first.stopped >= 1)
    monitor.close()


def test_excluded_foreground_holds_briefly_then_clears_without_measuring_others():
    own_pid = 4321
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log, self_pid=own_pid,
                       hold_seconds=2.0)  # 宽持有窗：sleep 在慢 runner 上 overshoot
                                         # 也不能越过它（否则 50ms 睡眠睡到 1s+
                                         # 就必然误停会话——macOS CI 实测）
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)

    fg.value = (own_pid, "dsh-pet.exe")      # 切到桌宠自己
    time.sleep(0.05)
    assert len(log.sessions) == 1, "短暂切走不该立刻换会话"
    assert log.sessions[0].stopped == 0
    assert _wait_until(lambda: log.latest.stopped >= 1, timeout=10.0)
    time.sleep(0.1)
    assert len(log.sessions) == 1, "切到自身不得改采别的进程"
    sample = monitor.snapshot()
    assert sample.state == gf.STATE_UNKNOWN and sample.fps is None
    assert sample.target_pid is None
    monitor.close()


def test_shell_foreground_is_never_targeted():
    fg = FakeForeground((999, "explorer.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_UNKNOWN)
    time.sleep(0.15)
    assert log.sessions == [], "桌面外壳不是游戏"
    monitor.close()


# ---------------------------------------------------------------- 权限与失败
PERMISSION_STDERR = (
    "error: failed to start trace session: access denied.\n"
    '       PresentMon requires either administrative privileges or to be run by a user in the\n'
    '       "Performance Log Users" user group.  View the readme for more details.\n'
)


def test_permission_denied_is_error_and_not_retried():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.exit(6, PERMISSION_STDERR)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_ERROR, timeout=3.0)
    sample = monitor.snapshot()
    assert "access denied" in sample.error_reason
    assert "Performance Log Users" in sample.error_reason
    assert sample.fps is None
    time.sleep(0.4)          # 远超 backoff / poll 周期
    assert len(log.sessions) == 1, "权限拒绝不许自动重试风暴"
    monitor.close()


def test_error_sample_stays_single_line_and_keeps_official_stderr():
    """段契约要求原因单行且有界；官方原文（证据）挂在详情里，不挤在卡片那行。"""
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.exit(6, PERMISSION_STDERR)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_ERROR, timeout=3.0)
    sample = monitor.snapshot()
    assert "\n" not in sample.error_reason and "\r" not in sample.error_reason
    assert len(sample.error_reason) <= seg.MAX_DETAIL_CHARS
    detail = sample.detail
    assert detail["unit_full"] == gf.UNIT_FULL and detail["source"] == gf.SOURCE_TEXT
    assert "access denied" in detail.get("backend_stderr", ""), "官方原文必须留档"
    assert len(detail["backend_stderr"]) <= gf.MAX_REASON_CHARS
    monitor.close()


def test_unexpected_failure_retries_bounded_then_errors():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog(exit_code=9, stderr="error: something went wrong\n")
    monitor = _monitor(foreground_reader=fg, session_factory=log, max_failures=3,
                       retry_backoff_seconds=0.05)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_ERROR, timeout=5.0)
    time.sleep(0.4)
    assert len(log.sessions) <= 3, "有界重试：不许无限重开会话"
    assert "9" in monitor.snapshot().error_reason
    monitor.close()


def test_normal_target_exit_goes_back_to_no_target_and_can_reopen():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    log.latest.exit(0, "")            # --terminate_on_proc_exit：目标退出
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_UNKNOWN, timeout=3.0)
    assert monitor.snapshot().fps is None
    fg.value = (555, "game-b.exe")
    assert _wait_until(lambda: len(log.sessions) == 2, timeout=3.0)
    monitor.close()


# ---------------------------------------------------------------- 订阅与生命周期
def test_unsubscribe_last_subscriber_stops_session_and_worker():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("a")
    monitor.subscribe("b")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    monitor.unsubscribe("a")
    time.sleep(0.1)
    assert len(log.sessions) == 1, "还有订阅者时不该停"
    assert monitor.running is True

    monitor.unsubscribe("b")
    assert _wait_until(lambda: log.latest.stopped >= 1 and log.latest.closed >= 1)
    assert _wait_until(lambda: monitor.running is False)
    monitor.close()


def test_stop_stops_session_and_restart_clears_old_value():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)

    monitor.stop(wait=True, timeout=3.0)
    assert monitor.running is False
    assert log.latest.stopped >= 1 and log.latest.closed >= 1

    monitor.start()               # 恢复：先清旧值，不许把上一版的 60 当新读数
    time.sleep(0.05)
    assert monitor.snapshot().state in (gf.STATE_SAMPLING, gf.STATE_UNKNOWN)
    assert monitor.snapshot().fps is None
    monitor.close()


def test_no_callback_after_stop():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    samples: list[fm.FpsSample] = []
    monitor = _monitor(foreground_reader=fg, session_factory=log,
                       on_sample=samples.append)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.feed_frames(_READING_FRAMES, with_header=True)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
    monitor.stop(wait=True, timeout=3.0)
    samples.clear()
    log.latest.feed_frames(50)
    time.sleep(0.2)
    assert samples == [], "停止后不许再有回调"
    monitor.close()


def test_repeated_start_stop_leaks_no_session_or_thread():
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    for round_index in range(4):
        monitor.start()
        assert _wait_until(lambda: len(log.sessions) >= round_index + 1)
        log.latest.feed_frames(_READING_FRAMES, with_header=True)
        _wait_until(lambda: monitor.snapshot().state == gf.STATE_OK)
        monitor.stop(wait=True, timeout=3.0)
        assert monitor.running is False
    assert len(log.sessions) == 4
    for session in log.sessions:
        assert session.stopped >= 1 and session.closed >= 1
    assert _live_threads() == 0
    monitor.close()


def test_close_is_idempotent_and_never_raises():
    monitor = _monitor()
    monitor.subscribe("fps")
    monitor.start()
    monitor.close()
    monitor.close()
    assert monitor.running is False


# ---------------------------------------------------------------- 配置
def test_process_mode_uses_name_lookup():
    lookup = FakeLookup((777, 5.0))
    log = SessionLog()
    monitor = _monitor(process_lookup=lookup, session_factory=log,
                       foreground_reader=FakeForeground((111, "other.exe")))
    monitor.configure(mode="process", process="game.exe")
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    assert log.latest.pid == 777
    assert lookup.calls and lookup.calls[0] == "game.exe"
    monitor.close()


def test_process_mode_missing_process_is_unknown_and_rescans_bounded():
    lookup = FakeLookup(None)
    log = SessionLog()
    monitor = _monitor(process_lookup=lookup, session_factory=log)
    monitor.configure(mode="process", process="game.exe")
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_UNKNOWN)
    assert "game.exe" in monitor.snapshot().error_reason
    assert log.sessions == []
    time.sleep(0.3)
    assert 2 <= len(lookup.calls) <= 30, "应当按 rescan 周期重试，且不能是忙等"
    monitor.close()


def test_configure_while_running_retargets_to_new_mode():
    lookup = FakeLookup((777, 5.0))
    log = SessionLog()
    monitor = _monitor(foreground_reader=FakeForeground((111, "game.exe")),
                       process_lookup=lookup, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    first = log.latest
    monitor.configure(mode="process", process="game.exe")
    assert _wait_until(lambda: len(log.sessions) == 2, timeout=3.0)
    assert log.latest.pid == 777
    assert _wait_until(lambda: first.stopped >= 1)
    monitor.close()


def test_reapplying_same_config_does_not_retry_but_switch_toggle_does():
    """终态错误的解除路径：**开关重开**或真的改了跟随设置。

    刻意不把"重复 apply 同一份配置"也算作重试信号：宿主可能周期性地回放配置，
    那会把一次权限不足变成无限重试（权限不会因为我们多试几次就变好）。
    """
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log)
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.exit(6, PERMISSION_STDERR)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_ERROR, timeout=3.0)
    time.sleep(0.3)
    assert len(log.sessions) == 1

    monitor.configure(mode="foreground", process="")     # 同一份配置的回放
    time.sleep(0.3)
    assert len(log.sessions) == 1, "重复回放配置不该解除终态（否则就是重试风暴）"

    monitor.stop()
    monitor.start()                                      # 用户把开关重开 = 显式重试
    assert _wait_until(lambda: len(log.sessions) == 2, timeout=3.0)
    monitor.close()


def test_configure_change_clears_terminal_error():
    """真的改了跟随方式（前台 ↔ 指定程序）＝ 换了目标，终态也随之解除。"""
    fg = FakeForeground((111, "game.exe"))
    log = SessionLog()
    monitor = _monitor(foreground_reader=fg, session_factory=log,
                       process_lookup=FakeLookup((777, 5.0)))
    monitor.subscribe("fps")
    monitor.start()
    assert _wait_until(lambda: log.sessions)
    log.latest.exit(6, PERMISSION_STDERR)
    assert _wait_until(lambda: monitor.snapshot().state == gf.STATE_ERROR, timeout=3.0)
    time.sleep(0.3)
    assert len(log.sessions) == 1

    monitor.configure(mode="process", process="game.exe")
    assert _wait_until(lambda: len(log.sessions) == 2, timeout=3.0)
    assert log.latest.pid == 777
    monitor.close()


def test_snapshot_always_returns_dataclass_and_never_raises():
    monitor = _monitor()
    for _ in range(50):
        sample = monitor.snapshot()
        assert isinstance(sample, fm.FpsSample)
        assert sample.fps is None and isinstance(sample.state, str)
    monitor.close()


def test_module_import_pulls_no_heavy_visual_dependency():
    """常驻采集模块不许把截图/视觉重依赖带进来（独立进程里核 sys.modules）。

    常驻路径每开一次会话/切一次前台都要用，把 PySide6 / numpy / 截图那套拽进来
    就是给整个桌宠加启动成本（vision 模块 import 实测 158ms，见 HANDOFF）。
    """
    import subprocess as sp

    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys, importlib\n"
        "importlib.import_module('pet.island.fps_monitor')\n"
        "print('numpy' in sys.modules, 'pet.vision' in sys.modules, 'PySide6' in sys.modules)\n"
    )
    proc = sp.run([sys.executable, "-c", code], cwd=str(repo),
                  capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["False", "False", "False"], proc.stdout


# ---------------------------------------------------------------- 真实会话（替身 Popen）
class _FakeStream:
    """按 ``io.BufferedReader.readline`` 语义的假流：最多返回 size 个字符。"""

    def __init__(self, lines):
        self._buffer = "".join(lines)
        self.closed = False

    def readline(self, size=-1):
        if not self._buffer:
            return ""
        if size is None or size < 0:
            size = len(self._buffer)
        cut = self._buffer.find("\n")
        end = len(self._buffer) if cut < 0 else cut + 1
        end = min(end, size)
        line, self._buffer = self._buffer[:end], self._buffer[end:]
        return line

    def close(self):
        self.closed = True


class _FakeProc:
    """真 Popen 的替身：够 FpsSession 用（poll/terminate/kill/wait/communicate + 两路流）。

    ``stdout_script`` / ``stderr_script`` 按 spawn 次序逐份喂给对应流（读线程在
    ``start()`` 那一刻就抓住了流对象，事后替换实例属性是抓不到的）。
    ``--terminate_existing_session`` 一起就是"官方终止命令把会话停掉、采集进程随即
    自行退出"的真机行为；``ignore_terminate_command`` 用来模拟"终止命令没兑现"。
    """

    instances: list["_FakeProc"] = []
    stdout_script: list[list[str]] = []
    stderr_script: list[list[str]] = []
    ignore_terminate_command = False

    def __init__(self, argv, **kwargs):
        self.argv = [str(a) for a in argv]
        self.pid = 4242
        self.returncode = None
        self.kwargs = dict(kwargs)
        stdout_lines = _FakeProc.stdout_script.pop(0) if _FakeProc.stdout_script else []
        stderr_lines = _FakeProc.stderr_script.pop(0) if _FakeProc.stderr_script else []
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.terminated = 0
        self.killed = 0
        _FakeProc.instances.append(self)
        if "--terminate_existing_session" in self.argv and not _FakeProc.ignore_terminate_command:
            for proc in _FakeProc.instances[:-1]:
                proc.returncode = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = 1

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        return "", ""


@pytest.fixture
def fake_popen(monkeypatch):
    _FakeProc.instances = []
    _FakeProc.stdout_script = []
    _FakeProc.stderr_script = []
    _FakeProc.ignore_terminate_command = False
    adopted: list[object] = []
    monkeypatch.setattr(fm.subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(fm.win_job, "adopt", lambda proc: adopted.append(proc) or True)
    return _FakeProc.instances, adopted


def _backend_file(tmp_path) -> Path:
    exe = tmp_path / gf.BACKEND_EXE_NAME
    exe.write_bytes(b"MZ")
    return exe


def _collect(session, sink: list[str]) -> bool:
    sink.extend(session.drain_lines())
    return bool(sink)


def test_real_session_spawns_pid_scoped_args_and_stops_gracefully(fake_popen, tmp_path):
    spawned, adopted = fake_popen
    session = fm.FpsSession(pid=4242, name="game.exe", backend=_backend_file(tmp_path),
                            session_name="dsh-pet-fps-1-ab", stop_timeout=0.2)
    session.start()
    assert len(spawned) == 1
    argv = spawned[0].argv
    assert argv[0] == str(_backend_file(tmp_path)) or argv[0].endswith(gf.BACKEND_EXE_NAME)
    assert argv[argv.index("--session_name") + 1] == "dsh-pet-fps-1-ab"
    assert argv[argv.index("--process_id") + 1] == "4242"
    for forbidden in ("--stop_existing_session", "--restart_as_admin", "--output_file"):
        assert forbidden not in argv
    assert adopted, "子进程必须挂进 job（父进程被强杀时不残留采集进程）"

    session.stop()
    assert len(spawned) == 2, "收口靠官方终止命令，不是 CTRL_BREAK/信号"
    terminate_argv = spawned[1].argv
    assert terminate_argv[-1] == "--terminate_existing_session"
    assert terminate_argv[terminate_argv.index("--session_name") + 1] == "dsh-pet-fps-1-ab"
    assert not any("BREAK" in " ".join(p.argv) for p in spawned)
    assert spawned[0].terminated == 0, "官方终止命令兑现时不该再硬杀"
    session.close()


def test_real_session_can_stop_without_spawning_the_terminate_command(fake_popen, tmp_path):
    """会话结束（关机/注销）路径：**不派生任何进程**，直接有界硬停。

    官方 ``--terminate_existing_session`` 是我们唯一能保证释放 ETW 会话的方式，但它
    本身是一条**新进程**——而关机窗口正是最不该再派生进程的时刻（issue #111 的
    0xc0000142 就是会话拆除阶段 CreateProcess 出来的进程在 DLL 初始化时失败）。所以
    那条路径改成"只停不派生"：采集进程照样收掉（terminate → kill 有界升级），只是不再
    补一条终止命令。本用例锁住"那次收口不许再 spawn"。
    """
    spawned, _adopted = fake_popen
    session = fm.FpsSession(pid=1, name="g.exe", backend=_backend_file(tmp_path),
                            session_name="s", stop_timeout=5.0)
    session.start()
    started = time.monotonic()
    session.stop(terminate_session=False)
    elapsed = time.monotonic() - started
    assert len(spawned) == 1, "会话结束路径不许再派生终止命令进程"
    assert spawned[0].terminated >= 1, "不派生也要把采集进程收掉（硬停）"
    assert elapsed < 2.0, "没有可等的自退路径：不许白等满 stop_timeout（关机路径要快）"
    session.close()


def test_monitor_close_forwards_the_no_spawn_flag_to_the_session():
    """退出编排的透传：正常退出用官方终止命令，会话结束用 no-spawn。"""
    for terminate_session, expected in ((True, True), (False, False)):
        log = SessionLog()
        monitor = _monitor(session_factory=log,
                           foreground_reader=FakeForeground((4242, "game.exe")))
        monitor.subscribe("fps")
        monitor.start()
        assert _wait_until(lambda: log.sessions and log.sessions[-1].started), "会话没开起来"
        monitor.close(terminate_session=terminate_session)
        assert log.latest.terminate_flags, "收口必须真的走到 session.stop"
        assert log.latest.terminate_flags[-1] is expected


def test_real_session_stop_without_live_process_is_noop(fake_popen, tmp_path):
    spawned, _adopted = fake_popen
    session = fm.FpsSession(pid=1, name="g.exe", backend=_backend_file(tmp_path),
                            session_name="s", stop_timeout=0.2)
    session.start()
    spawned[0].returncode = 6
    session.stop()
    assert len(spawned) == 1, "进程已退出就不再补一条终止命令"
    session.close()


def test_real_session_escalates_bounded_when_session_will_not_stop(fake_popen, tmp_path):
    """终止命令没兑现（会话还活着）时有界升级：terminate → kill，不无限等待。"""
    spawned, _adopted = fake_popen
    _FakeProc.ignore_terminate_command = True
    session = fm.FpsSession(pid=1, name="g.exe", backend=_backend_file(tmp_path),
                            session_name="s", stop_timeout=0.05)
    session.start()
    started = time.monotonic()
    session.stop()
    assert time.monotonic() - started < 2.0, "收口必须有界"
    assert spawned[0].terminated >= 1
    session.close()


def test_real_session_bounds_line_length(fake_popen, tmp_path):
    spawned, _adopted = fake_popen
    huge = "x" * (gf.MAX_LINE_CHARS * 2) + "\n"
    _FakeProc.stdout_script = [[huge, _HEADER + "\n"]]
    session = fm.FpsSession(pid=1, name="g.exe", backend=_backend_file(tmp_path),
                            session_name="s", stop_timeout=0.2)
    session.start()
    sink: list[str] = []
    assert _wait_until(lambda: _collect(session, sink))
    assert all(len(line) <= gf.MAX_LINE_CHARS + 1 for line in sink)
    assert not any(line.startswith("xxxx") for line in sink), "超长行必须整行丢弃"
    assert any(line.startswith("Application,") for line in sink), "后面的正常行不受影响"
    session.close()


def test_real_session_surfaces_stderr_tail(fake_popen, tmp_path):
    spawned, _adopted = fake_popen
    _FakeProc.stderr_script = [["error: failed to start trace session: access denied.\n"]]
    session = fm.FpsSession(pid=1, name="g.exe", backend=_backend_file(tmp_path),
                            session_name="s", stop_timeout=0.2)
    session.start()
    assert _wait_until(lambda: "access denied" in session.stderr_tail())
    session.close()


def test_real_session_rejects_missing_backend_before_spawning(fake_popen, tmp_path):
    spawned, _adopted = fake_popen
    session = fm.FpsSession(pid=1, name="g.exe", backend=tmp_path / "nope.exe",
                            session_name="s")
    with pytest.raises(fm.FpsSessionError):
        session.start()
    assert spawned == []


# ------------------------------------------------- 残留 ETW 会话清理（实机 1450 修复）
_SWEEP_LISTING = """\
Data Collector Set                      Type                          Status
-------------------------------------------------------------------------------
dsh-pet-fps-1-1-aabbccdd                Trace                         Running
dsh-pet-fps-2-2-11223344                Trace                         Running
SomeOtherCapture                        Trace                         Running
"""


class _FakeLogman:
    """``logman`` 调用替身（注意：不是进程替身，这里只关心 argv 与返回码）。"""

    def __init__(self, listing: str = "", stop_rc: int = 0, query_rc: int = 0):
        self.listing = listing
        self.stop_rc = stop_rc
        self.query_rc = query_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        is_query = len(argv) > 1 and argv[1] == "query"
        return subprocess.CompletedProcess(
            argv, self.query_rc if is_query else self.stop_rc,
            self.listing if is_query else "", "")


@pytest.fixture
def sweep_gate(monkeypatch):
    """每条用例都从「本进程还没扫过、也没建过会话」开始（产品里都是一次性状态）。"""
    monkeypatch.setattr(fm, "_swept_stale_sessions", False)
    monkeypatch.setattr(fm, "_own_session_names", set())


def test_sweep_stale_sessions_stops_only_own_prefix_and_spares_current(
        monkeypatch, sweep_gate):
    fake = _FakeLogman(_SWEEP_LISTING)
    monkeypatch.setattr(fm.subprocess, "run", fake)
    stopped = fm.sweep_stale_sessions("dsh-pet-fps-2-2-11223344")
    assert stopped == ["dsh-pet-fps-1-1-aabbccdd"], "只停自己前缀、且不是本次会话"
    assert [c[1] for c in fake.calls] == ["query", "stop"], "别人的采集会话不许碰"
    assert fake.calls[1] == ["logman", "stop", "dsh-pet-fps-1-1-aabbccdd", "-ets"]


def test_sweep_stale_sessions_survives_logman_failure(monkeypatch, sweep_gate):
    def boom(argv, **kwargs):
        raise OSError("logman 不存在")

    monkeypatch.setattr(fm.subprocess, "run", boom)
    assert fm.sweep_stale_sessions("s") == [], "清理失败绝不影响随后的采集启动"


def test_sweep_stale_sessions_runs_once_per_process(monkeypatch, sweep_gate):
    """一个进程只查一次：``logman query -ets`` 实测 970ms 中位。

    残留只可能来自上一个被强杀的进程（本进程每次停止都走官方 terminate），
    所以每换一次游戏目标都重查一遍纯属白付。
    """
    fake = _FakeLogman(_SWEEP_LISTING)
    monkeypatch.setattr(fm.subprocess, "run", fake)
    first = fm.sweep_stale_sessions("dsh-pet-fps-2-2-11223344")
    second = fm.sweep_stale_sessions("dsh-pet-fps-3-3-99999999")
    assert first == ["dsh-pet-fps-1-1-aabbccdd"]
    assert second == []
    assert [c[1] for c in fake.calls].count("query") == 1


def test_default_session_sweeps_before_building_the_session(monkeypatch, tmp_path):
    """开新采集会话前必须清一次残留（带本次会话名）。

    回归点：这一步曾经写在 ``FpsSession.start()`` 里，它要再起两个 ``logman``
    子进程，撞上会话自己的进程替身（``_FakeProc`` 不支持上下文管理器）。
    清残留是"准备新采集"的前置动作，归会话工厂。
    """
    sweeps: list[str] = []
    monkeypatch.setattr(fm, "sweep_stale_sessions",
                        lambda name: sweeps.append(name) or [])
    session = fm.default_session(1, "g.exe", "sess-1", backend=_backend_file(tmp_path))
    assert sweeps == ["sess-1"]
    assert session.session_name == "sess-1"


def test_sweep_never_stops_a_session_this_process_created(monkeypatch, sweep_gate):
    """本进程建过的会话一律不碰——只排除"本次要开的那个名字"不够。

    重叠时序：``stop(wait=False)`` 之后立刻 ``start()``，旧 worker 还卡在 970ms 的
    ``logman query`` 里，新 worker 已经把采集会话起来了；旧查询若只排除自己那个
    尚未启动的名字，就会把**新会话当残留停掉**，FPS 当场断掉。
    """
    listing = _SWEEP_LISTING + "dsh-pet-fps-9-9-ours                Trace    Running\n"
    fake = _FakeLogman(listing)
    monkeypatch.setattr(fm.subprocess, "run", fake)
    fm.sweep_stale_sessions("dsh-pet-fps-9-9-ours")  # 本进程的这一场
    fm.sweep_stale_sessions("dsh-pet-fps-3-3-next", once=False)
    stopped = [call[2] for call in fake.calls if call[1] == "stop"]
    assert "dsh-pet-fps-9-9-ours" not in stopped
    assert set(stopped) == {"dsh-pet-fps-1-1-aabbccdd", "dsh-pet-fps-2-2-11223344"}


def test_sweep_failure_leaves_the_gate_open(monkeypatch, sweep_gate):
    """查询失败不关闸：下一次开会话还会再试，不会"失败一次就永久不清理"。"""
    def boom(argv, **kwargs):
        raise OSError("logman 不存在")

    monkeypatch.setattr(fm.subprocess, "run", boom)
    assert fm.sweep_stale_sessions("a") == []
    assert fm._swept_stale_sessions is False


def test_sweep_keeps_the_gate_open_when_logman_reports_failure(
        monkeypatch, sweep_gate, caplog):
    """``logman`` 返回非零（会话表读不到）：不清理、留诊断，**闸门不关**。

    与发布稿「查询失败不关闸，下一次开会话再试」一致：只有真的拿到会话表才算扫过。
    闸门提前关掉的话，一次瞬时失败会让这个进程此后再也不清残留。
    """
    fake = _FakeLogman(_SWEEP_LISTING, query_rc=1)
    monkeypatch.setattr(fm.subprocess, "run", fake)
    with caplog.at_level(logging.WARNING):
        assert fm.sweep_stale_sessions("s") == []
    assert fm._swept_stale_sessions is False, "失败不关闸，下次还要再试"
    assert [call[1] for call in fake.calls] == ["query"], "读不到表就别去乱停"
    assert any("列出残留 ETW 会话失败" in record.getMessage()
               for record in caplog.records)

    # 下一次开会话再试：这次读到了，就该真清理并关闸
    ok = _FakeLogman(_SWEEP_LISTING)
    monkeypatch.setattr(fm.subprocess, "run", ok)
    assert fm.sweep_stale_sessions("s") == ["dsh-pet-fps-1-1-aabbccdd",
                                           "dsh-pet-fps-2-2-11223344"]
    assert fm._swept_stale_sessions is True
