# -*- coding: utf-8 -*-
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.webm_clip import WebMClip


def test_rapid_start_stop_no_leaked_running_threads():
    app = QApplication.instance() or QApplication([])

    sample_webm = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")
    assert sample_webm.exists(), f"WebM test file not found: {sample_webm}"

    clip = WebMClip(sample_webm)

    initial_threads = {t for t in threading.enumerate() if t.is_alive()}

    # 连续 start/stop 10 次
    for _ in range(10):
        clip.start()
        app.processEvents()
        clip.stop()
        app.processEvents()

    # 销毁/清理 clip，等待所有 retired 线程回收
    clip.cleanup()
    app.processEvents()

    # 断言 clip._retired 中的线程已全部结束
    for t in clip._retired:
        assert not t.is_alive()

    # 断言无残留运行中的 reader 线程（或整体 threading 运行线程无残留）。
    # 线程退出是异步的，给一点宽限时间再断言，避免 CI 偶发“线程尚未完全回收”。
    deadline = time.monotonic() + 5.0
    while True:
        alive_threads = {t for t in threading.enumerate() if t.is_alive()}
        new_alive = [t for t in alive_threads if t not in initial_threads]
        if not new_alive or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
        app.processEvents()
    assert len(new_alive) == 0, f"Remaining unexpected threads: {new_alive}"


def test_stale_generation_reader_writes_dropped(monkeypatch):
    app = QApplication.instance() or QApplication([])

    sample_webm = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")
    assert sample_webm.exists()

    clip = WebMClip(sample_webm)
    clip._ensure_meta()

    # 构造并启动一个 generation 为 1 的 reader 线程逻辑
    clip._generation = 1
    stop_evt = threading.Event()
    q = queue.Queue(maxsize=8)
    clip._queue = q

    # 模拟把 generation 提高为 2（表示新动画已启动），旧 generation 1 的 reader 尝试写入
    clip._generation = 2

    # 验证旧 reader 在 generation 不匹配时不会写入 self._queue 或 self._fps/duration
    old_fps = clip._fps
    clip._fps = 999.0

    # 运行 reader，由于 generation (1) != clip._generation (2)，reader 会迅速退出并不向队列或元数据写入
    clip._reader(stop_evt, generation=1)

    assert q.empty(), "Stale reader should not put items into queue"
    assert clip._fps == 999.0, "Stale reader should not overwrite metadata"

    clip.cleanup()
    app.processEvents()


def test_timer_is_precise_and_frame_interval_is_42ms():
    app = QApplication.instance() or QApplication([])

    assert catalog.FRAME_MS == 42

    sample_webm = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")
    clip = WebMClip(sample_webm) if sample_webm.exists() else WebMClip(__file__)

    assert clip._timer.timerType() == Qt.TimerType.PreciseTimer
    clip._fps = 24.0
    clip.playback_speed = 1.0
    assert clip._timer_interval() == 42

    clip.cleanup()
    app.processEvents()
