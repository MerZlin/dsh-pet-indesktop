# -*- coding: utf-8 -*-
"""Phase 2：动画预热门控测试（预热由省电模式联动，见 app._create_library）。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

import pet.library as library_mod
from pet.library import MovieLibrary


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_disabled_prewarm_does_not_schedule_low_or_high(tmp_path, monkeypatch):
    app = _qapp()

    class BoomThread:
        def __init__(self, *a, **kw):
            raise AssertionError("动画预热关闭时不应创建预热线程")

    monkeypatch.setattr(library_mod.threading, "Thread", BoomThread)
    lib = MovieLibrary(character_id="shenshen", prewarm_enabled=False)
    try:
        lib.schedule_high_priority_warm()  # 不应创建线程
        lib.schedule_low_priority_warm()  # 不应启动 2s 定时器
        assert lib._low_warm_timer.isActive() is False
        assert lib._prewarm_enabled is False
    finally:
        lib.deleteLater()
        app.processEvents()


def test_enable_prewarm_reschedules_when_visible(tmp_path, monkeypatch):
    app = _qapp()
    started_threads = []
    monkeypatch.setattr(
        library_mod.threading,
        "Thread",
        lambda *a, **kw: _CaptureThread(started_threads, *a, **kw),
    )
    lib = MovieLibrary(character_id="shenshen", prewarm_enabled=False)
    try:
        lib.set_prewarm_enabled(True, visible=True)
        assert lib._prewarm_enabled is True
        assert lib._low_warm_timer.isActive() is True
        assert started_threads, "开启预热后应重新发起高优先级预热线程"
    finally:
        lib._low_warm_timer.stop()
        lib.deleteLater()
        app.processEvents()


class _CaptureThread:
    def __init__(self, sink, *args, **kwargs):
        self.sink = sink
        self.args = args
        self.kwargs = kwargs
        self.daemon = kwargs.get("daemon", False)
        sink.append(self)

    def start(self):
        # 测试不真正拉起 ffmpeg 解码线程。
        return None
