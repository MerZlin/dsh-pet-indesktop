# -*- coding: utf-8 -*-
"""SMTC 有界读取：卡死的系统媒体服务不许把音乐识别永久带走。

现场：``GlobalSystemMediaTransportControlsSessionManager.RequestAsync()`` 在一台
真实机器上**永不返回**（PowerShell 5.1 的 .NET WinRT 投影同样超时，属系统级卡死）。
旧实现的超时是 ``asyncio.wait_for(_run_sync(...))``——卡的却是同步 COM 调用，
``wait_for`` 根本轮不上；于是采样线程永久停在 ``_collect()``，``_inflight`` 永远为
True，此后每一拍 ``poll()`` 都直接 return，识别彻底停摆。

这里的契约：
- ``read()`` 有界（超时回 ``degraded=True`` 的样例，理由可展示）；
- 降级窗内不再派发（不排队、不刷屏）；
- 卡死的读取一旦恢复，下一次 ``read()`` 自动拿到真实结果并解除降级；
- ``stop()`` 有界，且收口后不再建线程。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_smtc_timeout_test_host"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")


def _wait_until(cond, timeout_s: float = 3.0) -> bool:
    """宽预算轮询（事件同步优先；这里等的是别的线程跑完一次阻塞调用）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _playback() -> "now_playing.Playback":
    return now_playing.Playback(
        title="晴天", artist="周杰伦", app_id="cloudmusic.exe", playing=True
    )


class _Gate:
    """第一次调用卡在闸门上，放开后返回真实样例；调用次数可数。"""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self.entered = threading.Event()

    def __call__(self, **_kwargs):
        self.calls += 1
        self.entered.set()
        self.gate.wait(10.0)
        return now_playing.SmtcSample(playback=_playback())


def test_a_wedged_read_returns_a_degraded_sample_instead_of_blocking() -> None:
    gate = _Gate()
    reader = now_playing.SmtcReader(reader=gate, timeout=0.05, degraded_seconds=30.0)
    try:
        started = time.monotonic()
        sample = reader.read(browser_media_enabled=False)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"读取必须快速返回退化样例（实测 {elapsed:.2f}s）"
        assert sample.degraded is True
        assert sample.failed is False  # 退化不是"这一拍读失败"（那会冻结上一拍状态）
        assert "超时" in sample.reason
        assert reader.degraded is True
    finally:
        gate.gate.set()
        reader.stop(1.0)


def test_the_degraded_window_does_not_dispatch_again() -> None:
    gate = _Gate()
    reader = now_playing.SmtcReader(reader=gate, timeout=0.05, degraded_seconds=30.0)
    try:
        assert reader.read(browser_media_enabled=False).degraded is True
        assert _wait_until(gate.entered.is_set, 1.0)
        dispatches = reader.dispatched
        started = time.monotonic()
        for _ in range(5):
            sample = reader.read(browser_media_enabled=False)
            assert sample.degraded is True
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, "降级窗内必须立即返回（不排队、不等超时）"
        assert reader.dispatched == dispatches, "降级窗内不许再派发读取"
    finally:
        gate.gate.set()
        reader.stop(1.0)


def test_a_recovered_reader_is_used_again_without_a_restart() -> None:
    gate = _Gate()
    reader = now_playing.SmtcReader(reader=gate, timeout=0.05, degraded_seconds=30.0)
    try:
        assert reader.read(browser_media_enabled=False).degraded is True
        gate.gate.set()  # 卡住的那次调用返回 → 线程空出来
        assert _wait_until(lambda: reader.degraded is False, 3.0)
        sample = reader.read(browser_media_enabled=False)
        assert sample.degraded is False
        assert sample.playback is not None and sample.playback.title == "晴天"
    finally:
        gate.gate.set()
        reader.stop(1.0)


def test_stop_is_bounded_and_closes_the_reader_thread() -> None:
    reader = now_playing.SmtcReader(
        reader=lambda **_kwargs: now_playing.SmtcSample(playback=_playback()),
        timeout=1.0,
    )
    assert reader.read().playback is not None
    assert reader.stop(2.0) is True
    assert reader.alive() is False


def test_a_stopped_reader_never_starts_threads_again() -> None:
    calls: list[int] = []

    def fake(**_kwargs):
        calls.append(1)
        return now_playing.SmtcSample(playback=_playback())

    reader = now_playing.SmtcReader(reader=fake, timeout=1.0)
    try:
        assert reader.read().playback is not None
        started = reader.dispatched
    finally:
        reader.stop(2.0)
    sample = reader.read()
    assert sample.failed is True
    assert reader.dispatched == started


def test_sampling_carries_the_degraded_flag_out() -> None:
    """采样层不能吞掉退化标志：判定层要靠它区分"读超时"和"这一拍抖动"。"""
    sampling = importlib.import_module(f"{HOST_PKG}.sampling")
    windows_seen: list[frozenset] = []

    def read_windows(targets):
        windows_seen.append(targets)
        return ()

    sample = sampling.collect(
        smtc=lambda **_kwargs: now_playing.SmtcSample(
            degraded=True, reason="读取系统媒体会话超时。"
        ),
        audio=lambda: (),
        windows=read_windows,
        clock=lambda: 0.0,
    )
    assert sample.smtc_degraded is True
    assert sample.smtc_error is False
    assert "超时" in sample.smtc_reason
    # 退化不许短路其余两源：窗口源是本机唯一还能用的来源
    assert len(windows_seen) == 1


def test_the_degraded_window_re_probes_after_it_expires() -> None:
    """降级窗到期仍卡死：退役那条线程、另起一条探测（否则"卡死即永久降级"）。"""
    gate = _Gate()
    reader = now_playing.SmtcReader(reader=gate, timeout=0.02, degraded_seconds=0.05)
    try:
        assert reader.read().degraded is True
        first = reader.dispatched
        time.sleep(0.08)  # 窗到期
        assert reader.read().degraded is True
        assert reader.dispatched == first + 1, "窗到期必须重新探测（否则永远切不回主源）"
        assert reader.degraded is True
    finally:
        gate.gate.set()
        reader.stop(1.0)


def test_stuck_thread_retries_are_bounded() -> None:
    """每次重试都要退役一条卡死的线程（Python 不能杀线程）：必须封顶。"""
    gate = _Gate()
    reader = now_playing.SmtcReader(reader=gate, timeout=0.02, degraded_seconds=0.0)
    try:
        for _ in range(now_playing.SMTC_MAX_STUCK_THREADS + 4):
            assert reader.read().degraded is True
        assert reader.dispatched == now_playing.SMTC_MAX_STUCK_THREADS + 1
        sample = reader.read()
        assert sample.degraded is True
        assert "停止重试" in sample.reason
        assert reader.dispatched == now_playing.SMTC_MAX_STUCK_THREADS + 1
    finally:
        gate.gate.set()
        reader.stop(1.0)
