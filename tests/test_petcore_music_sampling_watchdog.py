# -*- coding: utf-8 -*-
"""采样链路的自愈：一拍卡死不许冻结此后所有拍，单源异常不许杀采样线程。

两类现场：

1. SMTC 卡死（``RequestAsync`` 永不返回）——读取线程被占住时，``_inflight`` 会一直
   挂着；``poll()`` 若只按这个标志早退，识别就会永久停摆。看门狗按时间作废那一拍。
2. 采集编排抛异常——``_worker_loop`` 没有兜底时线程直接死掉（``poll()`` 会重拉，
   但在飞标志仍是 True，等于换了个线程继续停摆）。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from petcore.contracts.host import HostPorts

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_watchdog_test_host"


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
sampling = importlib.import_module(f"{HOST_PKG}.sampling")
runtime_mod = importlib.import_module(f"{HOST_PKG}.runtime")


def _wait_until(cond, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class _Presentation:
    def __init__(self):
        self.hidden: list[str] = []

    def say(self, *_args, **_kwargs):
        return None

    def hide(self, owner=""):
        self.hidden.append(owner)

    def play_animation(self, *_args, **_kwargs):
        return False

    def release_loop(self):
        return True

    def play_event(self, *_args, **_kwargs):
        return False

    def show_image(self, *_args, **_kwargs):
        return False

    def notify(self, *_args, **_kwargs):
        return None


class _Config(dict):
    def set(self, key, value):
        self[key] = value

    def get(self, key, default=None):
        return dict.get(self, key, default)


def _playback() -> "now_playing.Playback":
    return now_playing.Playback(
        title="晴天",
        artist="周杰伦",
        app_id="cloudmusic.exe",
        position=0.0,
        duration=180.0,
        playing=True,
    )


class _Harness:
    def __init__(self) -> None:
        self.presentation = _Presentation()
        self.ports = HostPorts(
            presentation=self.presentation, config=_Config({"music_lyric_enabled": False})
        )
        self.runtime = runtime_mod.MusicRuntime(SimpleNamespace(ports=self.ports))
        self.now = 100.0
        self.runtime.clock = lambda: self.now
        self.runtime.sample_smtc = lambda **_kwargs: now_playing.SmtcSample(
            playback=_playback()
        )
        self.runtime.sample_audio = lambda: ()
        self.runtime.sample_windows = lambda _targets: ()
        self.runtime.background = lambda work: work()
        self.runtime.spawn_task = lambda work: work()


def test_a_stuck_beat_is_dropped_instead_of_blocking_every_later_beat() -> None:
    """上一拍卡在采集里：超过预算就作废它，下一拍照常派发（不永久停摆）。"""
    harness = _Harness()
    runtime = harness.runtime
    runtime.start()
    worker = runtime._worker
    try:
        runtime._inflight = True
        runtime._inflight_since = harness.now
        harness.now += runtime_mod.INFLIGHT_LIMIT_SECONDS + 1.0
        runtime._pending = None
        runtime.poll()
        assert runtime._pending is not None, "卡死的那一拍必须被作废，下一拍要重新派发"
        assert runtime._inflight is True
    finally:
        runtime.stop()
        if worker is not None:
            worker.join(5.0)


def test_a_fresh_beat_is_not_dropped_while_the_previous_one_is_running() -> None:
    """正常的在飞（未超预算）仍然跳过这一拍：不排队、不堆积。"""
    harness = _Harness()
    runtime = harness.runtime
    runtime.start()
    worker = runtime._worker
    try:
        runtime._inflight = True
        runtime._inflight_since = harness.now
        harness.now += runtime_mod.INFLIGHT_LIMIT_SECONDS - 0.5
        runtime._pending = None
        runtime.poll()
        assert runtime._pending is None
    finally:
        runtime.stop()
        if worker is not None:
            worker.join(5.0)


def test_a_collecting_beat_that_raises_never_kills_the_sampling_worker() -> None:
    """采集编排抛异常：这一拍折成失败样例投递，线程活着、在飞标志放开。"""
    harness = _Harness()
    runtime = harness.runtime
    runtime.start()
    worker = runtime._worker
    calls: list[int] = []

    def boom(_request):
        calls.append(1)
        raise RuntimeError("boom")

    runtime._collect = boom
    try:
        runtime.poll()
        assert _wait_until(lambda: bool(calls), 3.0)
        assert _wait_until(lambda: runtime._inflight is False, 3.0), (
            "异常的一拍必须放开在飞标志，否则此后每一拍都会被挡在门外"
        )
        assert worker is not None and worker.is_alive(), "采样线程不许因单源异常退出"
        runtime._collect = runtime.__class__._collect.__get__(runtime)
        runtime.poll()
        assert _wait_until(lambda: runtime.last_sample is not None, 3.0)
    finally:
        runtime.stop()
        if worker is not None:
            worker.join(5.0)


def test_sampling_exception_sample_is_reported_as_a_read_failure() -> None:
    """兜底样例的语义：``smtc_error``（本拍读失败）而不是"没在放"。"""
    harness = _Harness()
    runtime = harness.runtime
    runtime.start()
    worker = runtime._worker
    try:
        runtime._collect = lambda _request: (_ for _ in ()).throw(RuntimeError("boom"))
        runtime.poll()
        assert _wait_until(lambda: runtime.last_decision is not None, 3.0)
        assert runtime.last_decision.status == "error"
        assert runtime.last_sample is not None and runtime.last_sample.smtc_error is True
    finally:
        runtime.stop()
        if worker is not None:
            worker.join(5.0)


def test_start_installs_the_bounded_reader_and_poll_lands_a_degraded_sample() -> None:
    """接管链：读了 reader 的采样线程要把退化样例交给判定层，stop() 后解绑。"""
    harness = _Harness()
    runtime = harness.runtime
    gate = threading.Event()
    stuck_calls: list[int] = []

    def wedged(**_kwargs):
        stuck_calls.append(1)
        gate.wait(10.0)
        return now_playing.SmtcSample(playback=_playback())

    runtime._smtc_reader = now_playing.SmtcReader(reader=wedged, timeout=0.05)
    runtime.start()
    worker = runtime._worker
    try:
        runtime.poll()
        assert _wait_until(lambda: runtime.last_sample is not None, 3.0)
        assert runtime.last_sample.smtc_degraded is True
        assert _wait_until(lambda: runtime._inflight is False, 3.0)
    finally:
        runtime.stop()
        gate.set()
        if worker is not None:
            worker.join(5.0)
    assert runtime._smtc_reader is None
    assert stuck_calls  # 确实走了 reader（不是同步接缝）


def test_a_single_silent_beat_does_not_stop_playback() -> None:
    """峰值门限的单拍抖动：第一拍保留在播，第 2 拍才认暂停（否则 1Hz 释放—重播）。"""
    recognizer = importlib.import_module(f"{HOST_PKG}.recognizer")
    harness = _Harness()
    runtime = harness.runtime
    playing = recognizer.Decision(status=recognizer.STATUS_PLAYING)
    soft_pause = recognizer.Decision(
        status=recognizer.STATUS_PAUSED, soft_paused=True, reasons=("peak 低于门限",)
    )

    runtime._on_decision(playing, sampling.Sample())
    assert runtime._playing_now is True

    runtime._on_decision(soft_pause, sampling.Sample())
    assert runtime._playing_now is True, "单拍静音不许把在播立刻收掉"
    assert harness.presentation.hidden == []

    runtime._on_decision(soft_pause, sampling.Sample())
    assert runtime._playing_now is False, "连续两拍确认后才认暂停"
