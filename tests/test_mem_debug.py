# -*- coding: utf-8 -*-
"""内存取证计数器（pet/mem_debug.py）的单元面。

只验证「默认关闭、开启后能记账、行格式稳定、队列登记不延长寿命」——
取证本身不改产品行为，所以这里的断言都围绕「关掉时零副作用 / 打开时如实计数」。
"""
from __future__ import annotations

import queue
import threading
import time

from pet import mem_debug


def test_env_flag_parsing(monkeypatch):
    monkeypatch.delenv(mem_debug.ENV_FLAG, raising=False)
    assert mem_debug._env_flag() is False
    for value in ('', '0', 'off', 'no'):
        monkeypatch.setenv(mem_debug.ENV_FLAG, value)
        assert mem_debug._env_flag() is False, value
    for value in ('1', 'true', 'YES', 'On'):
        monkeypatch.setenv(mem_debug.ENV_FLAG, value)
        assert mem_debug._env_flag() is True, value


def _boom():
    raise RuntimeError('采集器异常必须被吞掉，绝不影响日志行')


def test_counters_collector_and_line_format():
    mem_debug.set_enabled(True, interval_s=3600)  # ticker 不会在本用例内触发
    try:
        mem_debug.bump('frames', 3)
        mem_debug.bump('frames')
        mem_debug.register_collector(lambda: {'extra': 7})
        mem_debug.register_collector(_boom)  # 抛异常的采集器被单独吞掉
        line = mem_debug.format_line()
        assert '[MEM]' in line and 't=' in line
        assert 'frames=4' in line
        assert 'extra=7' in line
        assert 'py_blocks=' in line
        assert mem_debug.snapshot()['frames'] == 4
    finally:
        mem_debug._reset_for_tests()
    assert mem_debug.ENABLED is False


def test_reader_registry_tracks_live_threads_only():
    mem_debug.set_enabled(True, interval_s=3600)
    q: queue.Queue = queue.Queue()
    stop = threading.Event()

    def _worker():
        stop.wait(5.0)

    thread = threading.Thread(target=_worker, name='mem-debug-test-worker')
    thread.start()
    try:
        mem_debug.note_reader('x#1', q, thread, 4)
        q.put((b'1234', 0))
        samples = {label: (frames, nbytes)
                   for label, frames, nbytes in mem_debug.live_readers()}
        assert samples['x#1'] == (1, 4)
        stop.set()
        thread.join(timeout=5)
        assert 'x#1' not in {label for label, _f, _b in mem_debug.live_readers()}
    finally:
        stop.set()
        mem_debug._reset_for_tests()


def test_webm_clip_collector_reports_display_slots():
    """webm_clip 的采集器必须如实报「持有显示帧的 clip 数与字节」。"""
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication

    from pet import webm_clip

    QApplication.instance() or QApplication([])
    mem_debug.set_enabled(True, interval_s=3600)  # clip 构造时才会登记进弱集合
    clip = webm_clip.WebMClip('x.webm')
    try:
        img = QImage(4, 2, QImage.Format.Format_RGBA8888)
        clip._current_image = img
        clip._current_pixmap = QPixmap.fromImage(img)
        data = webm_clip._mem_debug_collector()
        assert data['img_clips'] >= 1
        assert data['img_bytes'] >= 4 * 2 * 4
        assert data['pm_clips'] >= 1
        assert data['pm_bytes'] >= 4 * 2 * 4
        assert 'reader_q' in data and 'ff_bytes' in data and 'clips' in data
    finally:
        clip.cleanup()
        mem_debug._reset_for_tests()


def test_counters_are_noop_when_disabled():
    """默认关闭时：登记表为空、不建 ticker 线程。"""
    mem_debug._reset_for_tests()
    before = {t.name for t in threading.enumerate()}
    assert mem_debug.live_readers() == []
    assert mem_debug.ENABLED is False
    time.sleep(0.01)
    after = {t.name for t in threading.enumerate()}
    assert 'mem-debug-ticker' not in after - before
